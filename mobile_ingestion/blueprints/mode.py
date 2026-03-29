from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, current_app, jsonify, stream_with_context

from mobile_ingestion.dto import RuntimeModeStatusDto
from mobile_ingestion.mode_manager import (RuntimeModeEvent, RuntimeModePort,
                                           RuntimeModeStatus)


mode_blueprint = Blueprint("mode", __name__, url_prefix="/api/mode")


def _runtime_mode() -> RuntimeModePort:
  services = current_app.extensions["mobile_ingestion.services"]
  return services.runtime_mode


@mode_blueprint.get("/status")
def status() -> tuple[dict[str, object], int]:
  return jsonify(RuntimeModeStatusDto.from_status(_runtime_mode().snapshot()).to_dict()), 200


@mode_blueprint.get("/events")
def events() -> Response:
  manager = _runtime_mode()
  subscription = manager.subscribe()

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
      manager.unsubscribe(subscription)

  return Response(
      stream_with_context(generate()),
      mimetype="text/event-stream",
      headers={
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
      },
  )


def _encode_sse(event: RuntimeModeEvent) -> str:
  return f"event: {event.event_type}\ndata: {json.dumps(_event_payload(event))}\n\n"


def _event_payload(event: RuntimeModeEvent) -> dict[str, object]:
  payload = event.payload
  if isinstance(payload, RuntimeModeStatus):
    return RuntimeModeStatusDto.from_status(payload).to_dict()
  raise ValueError(f"Unsupported mode event type: {event.event_type}")
