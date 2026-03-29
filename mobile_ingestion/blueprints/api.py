from __future__ import annotations

import re

from flask import Blueprint, current_app, jsonify, request

from mobile_ingestion.command_classifier import CommandClassifier
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.session_manager import (InvalidSessionError,
                                              SessionBusyError)


api_blueprint = Blueprint("api", __name__, url_prefix="/api/webrtc")
command_classifier = CommandClassifier()

_LOCATOR_QUERY_PATTERN = re.compile(
    r"\b(where|wheres|ou|find|locate|spot|trouve|trouver|reper|repere)\b",
    re.IGNORECASE,
)
_NOISE_WORDS = {
    "where", "wheres", "is", "are", "the", "a", "an", "my", "your", "their", "his", "her",
    "ou", "est", "sont", "se", "trouve", "trouver", "trouve", "les", "des", "de", "du", "la", "le",
    "find", "locate", "spot", "please", "can", "you", "me", "for", "to", "dans", "sur", "ici",
}


def _extract_locator_target(text: str) -> str | None:
  raw = (text or "").strip()
  if not raw:
    return None
  if not _LOCATOR_QUERY_PATTERN.search(raw):
    return None

  normalized = re.sub(r"[^a-zA-Z0-9\s]", " ", raw.lower())
  tokens = [token for token in normalized.split() if token]
  content_tokens = [token for token in tokens if token not in _NOISE_WORDS]
  if not content_tokens:
    return None

  return " ".join(content_tokens[-3:])


def _run_voice_agent(user_text: str,
           services) -> tuple[dict[str, object], int]:
  from mobile_ingestion.agent import generate_voice_response

  settings = services.settings
  if not settings.openrouter_api_key or not settings.elevenlabs_api_key:
    return {
        "command": "agent_error",
        "result": {
            "text": "Configuration manquante: OPENROUTER_API_KEY ou ELEVENLABS_API_KEY.",
            "audioData": None,
        },
    }, 503

  text_resp, audio_b64 = generate_voice_response(
      openrouter_api_key=settings.openrouter_api_key,
      elevenlabs_api_key=settings.elevenlabs_api_key,
      system_prompt=settings.llm_system_prompt,
      user_text=user_text,
  )
  if not text_resp:
    return {
        "command": "agent_error",
        "result": {
            "text": "Le service agent n'a pas retourne de texte.",
            "audioData": None,
        },
    }, 502

  if not audio_b64:
    return {
      "command": "agent_response",
        "result": {
        "text": text_resp,
            "audioData": None,
        "audioFallback": True,
        },
    }, 200

  return {
      "command": "agent_response",
      "result": {
          "text": text_resp,
          "audioData": audio_b64,
      },
  }, 200

def _run_visual_locator(user_text: str,
            target_object: str,
            services) -> tuple[dict[str, object], int]:
  from mobile_ingestion.agent import (detect_spatial_detection_from_image,
                                      synthesize_speech)

  settings = services.settings
  analyzer = services.analyzer
  frame = analyzer.get_latest_video_frame() if hasattr(
    analyzer, "get_latest_video_frame") else None
  if frame is None or not frame.jpeg_base64:
    text_response = (
      f"Je ne recois pas encore une image exploitable de la camera pour localiser '{target_object}'. "
      "Pointez le telephone vers la zone probable et reessayez dans une seconde."
    )
    spatial_detection = {
        "visible": False,
        "confidence": "low",
        "horizontal": "unknown",
        "vertical": "unknown",
        "depth": "unknown",
        "guidance": "Balayez lentement gauche-droite.",
        "summary": text_response,
        "targetObject": target_object,
    }
    audio = None
    if settings.elevenlabs_api_key:
      _, audio = synthesize_speech(settings.elevenlabs_api_key, text_response)
    return {
      "command": "agent_response",
      "result": {
        "text": text_response,
        "audioData": audio,
        "spatialDetection": spatial_detection,
      },
    }, 409

  if not settings.openrouter_api_key:
    return {
        "command": "agent_error",
        "result": {
            "text": "Configuration manquante: OPENROUTER_API_KEY.",
            "audioData": None,
        },
    }, 503
  if not settings.elevenlabs_api_key:
    return {
        "command": "agent_error",
        "result": {
            "text": "Configuration manquante: ELEVENLABS_API_KEY.",
            "audioData": None,
        },
    }, 503

  text_response, spatial_detection = detect_spatial_detection_from_image(
      openrouter_api_key=settings.openrouter_api_key,
      user_text=user_text,
      object_name=target_object,
      jpeg_base64=frame.jpeg_base64,
  )
  if not text_response:
    text_response = "Je ne peux pas localiser l'objet pour le moment."

  if spatial_detection is not None:
    spatial_detection["targetObject"] = target_object

  error, audio = synthesize_speech(settings.elevenlabs_api_key, text_response)
  return {
      "command": "agent_response",
      "result": {
          "text": error or text_response,
          "audioData": audio,
          "spatialDetection": spatial_detection,
      },
  }, 200


@api_blueprint.post("/spatial-detection")
def spatial_detection() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  user_text = payload.get("text")
  target_raw = payload.get("targetObject")
  if isinstance(target_raw, str) and target_raw.strip():
    target_object = target_raw.strip()
  else:
    target_object = _extract_locator_target(user_text if isinstance(user_text, str) else "")
  if not target_object:
    target_object = "object"

  if not isinstance(user_text, str) or not user_text.strip():
    user_text = f"localise {target_object}"

  services = current_app.extensions["mobile_ingestion.services"]
  response_payload, status_code = _run_visual_locator(user_text.strip(),
                                                      target_object,
                                                      services)
  response_payload["command"] = "spatial_detection"
  return jsonify(response_payload), status_code


@api_blueprint.get("/status")
def status() -> tuple[dict[str, object], int]:
  services = current_app.extensions["mobile_ingestion.services"]
  return jsonify(services.session_manager.get_status().to_dict()), 200


@api_blueprint.post("/offer")
def offer() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400
  services = current_app.extensions["mobile_ingestion.services"]
  current_app.logger.info(
      "POST /api/webrtc/offer remote=%s ua=%s keys=%s",
      request.remote_addr,
      request.headers.get("User-Agent", "-")[:120],
      sorted(payload.keys()),
  )

  try:
    offer_dto = SessionDescriptionDto.from_mapping(payload)
    answer = services.session_manager.accept_offer(offer_dto)
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400
  except InvalidSessionError as exc:
    return jsonify({"error": str(exc)}), 400
  except SessionBusyError as exc:
    current_app.logger.warning("Offer rejected: session busy remote=%s",
                               request.remote_addr)
    return jsonify({"error": str(exc)}), 409
  except RuntimeError as exc:
    current_app.logger.exception("Failed to negotiate WebRTC offer.")
    return jsonify({"error": str(exc)}), 500

  status_payload = services.session_manager.get_status().to_dict()
  response_payload = answer.to_dict()
  response_payload["sessionId"] = status_payload.get("sessionId")
  current_app.logger.info(
      "Offer negotiated session_id=%s connection_state=%s",
      response_payload.get("sessionId"),
      status_payload.get("connectionState"),
  )
  return jsonify(response_payload), 200


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

  current_app.logger.info(
      "POST /api/webrtc/command remote=%s keys=%s forceAgent=%s forceLocator=%s",
      request.remote_addr,
      sorted(payload.keys()),
      bool(payload.get("forceAgent", False)),
      bool(payload.get("forceLocator", False)),
  )

  services = current_app.extensions["mobile_ingestion.services"]
  event_broadcaster = current_app.extensions.get("mobile_ingestion.event_broadcaster")
  status = services.session_manager.get_status()
  active_session_id = getattr(status, "session_id", None)
  if not isinstance(active_session_id, str) or not active_session_id:
    try:
      status_payload = status.to_dict() if hasattr(status, "to_dict") else {}
      session_id_value = status_payload.get("sessionId")
      if isinstance(session_id_value, str) and session_id_value:
        active_session_id = session_id_value
    except Exception:
      active_session_id = None
  services.session_manager.record_activity()
  force_agent = bool(payload.get("forceAgent", False))
  force_locator = bool(payload.get("forceLocator", False))

  if force_agent:
    candidate_text = payload.get("text")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
      candidate_text = payload.get("command")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
      return jsonify({
          "error": "Field 'text' or 'command' must be a non-empty string when forceAgent=true.",
      }), 400
    try:
      response_payload, status_code = _run_voice_agent(candidate_text.strip(),
                                                       services)
      if event_broadcaster is not None and active_session_id:
        event_broadcaster.broadcast(active_session_id, "cmd", {
            "text": candidate_text.strip(),
            "source": "voice.forceAgent",
        })
      return jsonify(response_payload), status_code
    except Exception:
      current_app.logger.exception("Forced agent generation failed")
      return jsonify({
          "command": "agent_error",
          "result": {
              "text": "Une erreur est survenue lors de l'appel a l'agent vocal.",
              "audioData": None,
          },
      }), 500

  if force_locator:
    candidate_text = payload.get("text")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
      candidate_text = payload.get("command")
    if not isinstance(candidate_text, str) or not candidate_text.strip():
      return jsonify({
          "error": "Field 'text' or 'command' must be a non-empty string when forceLocator=true.",
      }), 400
    target_object = _extract_locator_target(candidate_text.strip()) or "object"
    response_payload, status_code = _run_visual_locator(candidate_text.strip(),
                                                        target_object,
                                                        services)
    if event_broadcaster is not None and active_session_id:
      event_broadcaster.broadcast(active_session_id, "cmd", {
          "text": candidate_text.strip(),
          "source": "voice.forceLocator",
      })
      if isinstance(response_payload, dict):
        result_payload = response_payload.get("result")
        if isinstance(result_payload, dict) and isinstance(
            result_payload.get("spatialDetection"), dict):
          event_broadcaster.broadcast(active_session_id, "spatial_result",
                                      result_payload["spatialDetection"])
    return jsonify(response_payload), status_code

  classified = command_classifier.classify(payload)

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
    result_payload = answer.to_dict()
    result_payload["sessionId"] = services.session_manager.get_status().to_dict().get(
      "sessionId")
    return jsonify({
      "command": classified.command,
      "result": result_payload,
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

  if classified.command == "spatial_detection":
    target_candidate = classified.arguments.get("targetObject")
    if isinstance(target_candidate, str) and target_candidate.strip():
      target_object = target_candidate.strip()
    else:
      target_object = _extract_locator_target(classified.raw_command or "") or "object"
    query_text = classified.raw_command or f"localise {target_object}"
    response_payload, status_code = _run_visual_locator(query_text,
                                                        target_object,
                                                        services)
    response_payload["command"] = "spatial_detection"
    if event_broadcaster is not None and active_session_id:
      event_broadcaster.broadcast(active_session_id, "cmd", {
          "text": query_text,
          "source": "classifier.spatial_detection",
      })
      result_payload = response_payload.get("result")
      if isinstance(result_payload, dict) and isinstance(
          result_payload.get("spatialDetection"), dict):
        event_broadcaster.broadcast(active_session_id, "spatial_result",
                                    result_payload["spatialDetection"])
    return jsonify(response_payload), status_code

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

  if classified.command == "unknown" and classified.raw_command:
    try:
      target_object = _extract_locator_target(classified.raw_command)
      if target_object:
        response_payload, status_code = _run_visual_locator(
            classified.raw_command,
            target_object,
            services)
        if event_broadcaster is not None and active_session_id:
          event_broadcaster.broadcast(active_session_id, "cmd", {
              "text": classified.raw_command,
              "source": "classifier.unknown_locator",
          })
        return jsonify(response_payload), status_code

      settings = services.settings
      if settings.openrouter_api_key and settings.elevenlabs_api_key:
        response_payload, status_code = _run_voice_agent(classified.raw_command,
                                                         services)
        if event_broadcaster is not None and active_session_id:
          event_broadcaster.broadcast(active_session_id, "cmd", {
              "text": classified.raw_command,
              "source": "classifier.unknown_agent",
          })
        return jsonify(response_payload), status_code
    except Exception:
      current_app.logger.exception("Agent generation failed")

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
            "spatial_detection",
          ],
          "note": "Provide a command via keys like 'command', 'action', or 'event'.",
      },
  }), 422
