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

  return jsonify({
      "command": "unknown",
      "rawCommand": classified.raw_command,
      "result": {
          "supportedCommands": ["offer", "status", "close_session", "ping"],
          "note": "Provide a command via keys like 'command', 'action', or 'event'.",
      },
  }), 422
