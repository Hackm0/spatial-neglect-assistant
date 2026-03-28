from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from mobile_ingestion.command_classifier import CommandClassifier
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.session_manager import (InvalidSessionError,
                                              SessionBusyError)


api_blueprint = Blueprint("api", __name__, url_prefix="/api/webrtc")
command_classifier = CommandClassifier()


@api_blueprint.get("/status")
def status() -> tuple[dict[str, object], int]:
  services = current_app.extensions["mobile_ingestion.services"]
  return jsonify(services.session_manager.get_status().to_dict()), 200


@api_blueprint.post("/offer")
def offer() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  try:
    offer_dto = SessionDescriptionDto.from_mapping(payload)
    answer = current_app.extensions[
        "mobile_ingestion.services"].session_manager.accept_offer(offer_dto)
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400
  except InvalidSessionError as exc:
    return jsonify({"error": str(exc)}), 400
  except SessionBusyError as exc:
    return jsonify({"error": str(exc)}), 409
  except RuntimeError as exc:
    current_app.logger.exception("Failed to negotiate WebRTC offer.")
    return jsonify({"error": str(exc)}), 500

  return jsonify(answer.to_dict()), 200


@api_blueprint.delete("/session")
def close_session() -> tuple[str, int]:
  current_app.extensions["mobile_ingestion.services"].session_manager.close_active_session(
  )
  return "", 204


@api_blueprint.get("/transcript")
def transcript() -> tuple[dict[str, object], int]:
  services = current_app.extensions["mobile_ingestion.services"]
  return jsonify(services.analyzer.transcript_snapshot().to_dict()), 200


@api_blueprint.post("/transcript")
def add_transcript() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  text = payload.get("text")
  if not isinstance(text, str) or not text.strip():
    return jsonify({"error": "Field 'text' must be a non-empty string."}), 400

  final = bool(payload.get("final", True))
  source_raw = payload.get("source", "server")
  source = source_raw if isinstance(source_raw, str) else "server"

  services = current_app.extensions["mobile_ingestion.services"]
  services.analyzer.on_transcript(text=text, final=final, source=source)
  return jsonify(services.analyzer.transcript_snapshot().to_dict()), 200


@api_blueprint.delete("/transcript")
def clear_transcript() -> tuple[str, int]:
  services = current_app.extensions["mobile_ingestion.services"]
  services.analyzer.clear_transcript()
  return "", 204


@api_blueprint.post("/command")
def command() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  classified = command_classifier.classify(payload)
  services = current_app.extensions["mobile_ingestion.services"]

  if classified.command == "offer":
    offer_payload = classified.arguments or payload
    try:
      offer_dto = SessionDescriptionDto.from_mapping(offer_payload)
      answer = services.session_manager.accept_offer(offer_dto)
    except ValueError as exc:
      return jsonify({"error": str(exc), "command": classified.command}), 400
    except InvalidSessionError as exc:
      return jsonify({"error": str(exc), "command": classified.command}), 400
    except SessionBusyError as exc:
      return jsonify({"error": str(exc), "command": classified.command}), 409
    except RuntimeError as exc:
      current_app.logger.exception("Failed to execute 'offer' command.")
      return jsonify({"error": str(exc), "command": classified.command}), 500
    return jsonify({
        "command": classified.command,
        "result": answer.to_dict(),
    }), 200

  if classified.command == "status":
    return jsonify({
        "command": classified.command,
        "result": services.session_manager.get_status().to_dict(),
    }), 200

  if classified.command == "close_session":
    services.session_manager.close_active_session()
    return jsonify({
        "command": classified.command,
        "result": services.session_manager.get_status().to_dict(),
    }), 200

  if classified.command == "ping":
    return jsonify({
        "command": classified.command,
        "result": {
            "ok": True,
        },
    }), 200

  if classified.command == "transcript_get":
    return jsonify({
        "command": classified.command,
        "result": services.analyzer.transcript_snapshot().to_dict(),
    }), 200

  if classified.command == "transcript_add":
    text_value = classified.arguments.get("text")
    if not isinstance(text_value, str) or not text_value.strip():
      return jsonify({
          "error": "Field 'text' must be a non-empty string.",
          "command": classified.command,
      }), 400
    final = bool(classified.arguments.get("final", True))
    source_raw = classified.arguments.get("source", "server")
    source = source_raw if isinstance(source_raw, str) else "server"
    services.analyzer.on_transcript(text=text_value, final=final, source=source)
    return jsonify({
        "command": classified.command,
        "result": services.analyzer.transcript_snapshot().to_dict(),
    }), 200

  if classified.command == "transcript_clear":
    services.analyzer.clear_transcript()
    return jsonify({
        "command": classified.command,
        "result": services.analyzer.transcript_snapshot().to_dict(),
    }), 200

  return jsonify({
      "command": "unknown",
      "rawCommand": classified.raw_command,
      "result": {
          "supportedCommands": [
            "offer",
            "status",
            "close_session",
            "ping",
            "transcript_get",
            "transcript_add",
            "transcript_clear",
          ],
          "note": "Provide a command via keys like 'command', 'action', or 'event'.",
      },
  }), 422
