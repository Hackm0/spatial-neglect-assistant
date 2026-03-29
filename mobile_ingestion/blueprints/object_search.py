from __future__ import annotations

import json
import queue

from flask import (Blueprint, Response, current_app, jsonify, request,
                   stream_with_context)

from mobile_ingestion.dto import ObjectSearchStatusDto
from mobile_ingestion.object_search import (ObjectSearchEvent, ObjectSearchPort,
                                            ObjectSearchStatus)
from mobile_ingestion.session_manager import (SESSION_TOKEN_HEADER,
                                              SessionAuthorizationError,
                                              SessionPermissionError)


object_search_blueprint = Blueprint("object_search",
                                    __name__,
                                    url_prefix="/api/object-search")


def _object_search() -> ObjectSearchPort:
  services = current_app.extensions["mobile_ingestion.services"]
  return services.object_search


def _require_sender_session() -> None:
  current_app.extensions["mobile_ingestion.services"].session_manager.assert_sender_session(
      request.headers.get(SESSION_TOKEN_HEADER))


@object_search_blueprint.get("/status")
def status() -> tuple[dict[str, object], int]:
  return jsonify(
      ObjectSearchStatusDto.from_status(_object_search().snapshot()).to_dict(),
  ), 200


@object_search_blueprint.put("/vision-model")
def update_vision_model() -> tuple[dict[str, object], int]:
  try:
    _require_sender_session()
  except SessionAuthorizationError as exc:
    return jsonify({"error": str(exc)}), 401
  except SessionPermissionError as exc:
    return jsonify({"error": str(exc)}), 403
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  model = payload.get("model")
  if not isinstance(model, str) or not model.strip():
    return jsonify({"error": "The vision model is required."}), 400

  try:
    status = _object_search().set_selected_vision_model(model)
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400

  return jsonify(ObjectSearchStatusDto.from_status(status).to_dict()), 200


@object_search_blueprint.get("/events")
def events() -> Response:
  processor = _object_search()
  subscription = processor.subscribe()

  def generate() -> object:
    try:
      while True:
        try:
          event = subscription.events.get(timeout=15.0)
        except queue.Empty:
          yield ": keep-alive\n\n"
          continue

        if event is None:
          break
        yield _encode_sse(event)
    finally:
      processor.unsubscribe(subscription)

  return Response(
      stream_with_context(generate()),
      mimetype="text/event-stream",
      headers={
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
      },
  )


def _encode_sse(event: ObjectSearchEvent) -> str:
  return f"event: {event.event_type}\ndata: {json.dumps(_event_payload(event))}\n\n"


def _event_payload(event: ObjectSearchEvent) -> dict[str, object]:
  payload = event.payload
  if isinstance(payload, ObjectSearchStatus):
    return ObjectSearchStatusDto.from_status(payload).to_dict()
  raise ValueError(f"Unsupported object-search event type: {event.event_type}")
