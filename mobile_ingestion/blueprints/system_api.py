from __future__ import annotations

import json
from time import time

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from mobile_ingestion.events import dequeue_with_timeout


system_api_blueprint = Blueprint("system_api", __name__, url_prefix="/api")


def _get_session_id(services, payload: dict[str, object] | None = None) -> str | None:
  requested = None
  if payload and isinstance(payload.get("sessionId"), str):
    requested = str(payload.get("sessionId")).strip() or None
  status = services.session_manager.get_status()
  active = getattr(status, "session_id", None)
  if not isinstance(active, str) or not active:
    try:
      status_payload = status.to_dict() if hasattr(status, "to_dict") else {}
      session_id_value = status_payload.get("sessionId")
      if isinstance(session_id_value, str) and session_id_value:
        active = session_id_value
    except Exception:
      active = None
  if not active:
    return None
  if requested and active and requested != active:
    return "__mismatch__"
  return active


@system_api_blueprint.post("/session/config")
def session_config() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  services = current_app.extensions["mobile_ingestion.services"]
  session_id = _get_session_id(services, payload)
  if session_id in {None, "__mismatch__"}:
    current_app.logger.warning(
        "Session config rejected remote=%s requested_session=%s reason=%s",
        request.remote_addr,
        payload.get("sessionId"),
        "mismatch" if session_id == "__mismatch__" else "not_found",
    )
    return jsonify({"error": "Session not found."}), 404

  if "autoOff" in payload:
    services.session_manager.set_auto_off_enabled(bool(payload["autoOff"]))
  current_app.logger.info(
      "Session config updated session_id=%s auto_off=%s",
      session_id,
      bool(payload.get("autoOff", False)),
  )

  services.session_manager.record_activity()
  broadcaster = current_app.extensions.get("mobile_ingestion.event_broadcaster")
  if broadcaster is not None:
    broadcaster.broadcast(session_id, "session_config", {
        "autoOff": bool(payload.get("autoOff", False)),
    })

  return jsonify({"ok": True}), 200


@system_api_blueprint.post("/session/heartbeat")
def heartbeat() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    payload = {}

  services = current_app.extensions["mobile_ingestion.services"]
  session_id = _get_session_id(services, payload)
  if session_id is None:
    current_app.logger.warning(
        "Heartbeat rejected remote=%s requested_session=%s reason=%s",
        request.remote_addr,
        payload.get("sessionId"),
        "not_found",
    )
    return jsonify({"ok": False, "reason": "session_not_found"}), 200

  if session_id == "__mismatch__":
    status = services.session_manager.get_status()
    active = getattr(status, "session_id", None)
    if not isinstance(active, str) or not active:
      try:
        status_payload = status.to_dict() if hasattr(status, "to_dict") else {}
        session_id_value = status_payload.get("sessionId")
        if isinstance(session_id_value, str) and session_id_value:
          active = session_id_value
      except Exception:
        active = None
    if not active:
      current_app.logger.warning(
          "Heartbeat rejected remote=%s requested_session=%s reason=mismatch_no_active",
          request.remote_addr,
          payload.get("sessionId"),
      )
      return jsonify({"ok": False, "reason": "session_not_found"}), 200

    current_app.logger.info(
        "Heartbeat session resync remote=%s requested_session=%s active_session=%s",
        request.remote_addr,
        payload.get("sessionId"),
        active,
    )
    session_id = active

  services.session_manager.record_activity()
  broadcaster = current_app.extensions.get("mobile_ingestion.event_broadcaster")
  if broadcaster is not None:
    broadcaster.broadcast(session_id, "heartbeat", {"at": time()})
  return jsonify({"ok": True, "sessionId": session_id}), 200


@system_api_blueprint.get("/events/<session_id>")
def event_stream(session_id: str) -> Response:
  broadcaster = current_app.extensions.get("mobile_ingestion.event_broadcaster")
  if broadcaster is None:
    return Response(status=503)

  def generate():
    for event in broadcaster.recent_events(session_id):
      yield f"data: {json.dumps(event)}\\n\\n"

    queue = broadcaster.subscribe(session_id)
    try:
      while True:
        event = dequeue_with_timeout(queue, 30.0)
        if event is None:
          yield 'data: {"type":"keepalive"}\\n\\n'
          continue
        yield f"data: {json.dumps(event)}\\n\\n"
    finally:
      broadcaster.unsubscribe(session_id, queue)

  return Response(
      stream_with_context(generate()),
      mimetype="text/event-stream",
      headers={
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
      },
  )


@system_api_blueprint.get("/debug")
def debug_snapshot() -> tuple[dict[str, object], int]:
  services = current_app.extensions["mobile_ingestion.services"]
  if not services.settings.debug:
    return jsonify({"error": "Not found."}), 404

  broadcaster = current_app.extensions.get("mobile_ingestion.event_broadcaster")
  status = services.session_manager.get_status()

  return jsonify({
      "status": status.to_dict(),
      "session": services.session_manager.debug_snapshot(),
      "events": broadcaster.snapshots() if broadcaster is not None else {},
  }), 200
