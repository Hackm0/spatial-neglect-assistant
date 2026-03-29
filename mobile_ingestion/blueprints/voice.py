from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, current_app, jsonify, stream_with_context

from mobile_ingestion.dto import TranscriptEntryDto, VoiceStatusDto, WakeWordEventDto
from mobile_ingestion.voice import (TranscriptEntry, VoiceEvent, VoiceProcessingPort,
                                    VoiceStatus, WakeWordEvent)


voice_blueprint = Blueprint("voice", __name__, url_prefix="/api/voice")


def _voice_processor() -> VoiceProcessingPort:
  services = current_app.extensions["mobile_ingestion.services"]
  return services.voice_processor


@voice_blueprint.get("/status")
def status() -> tuple[dict[str, object], int]:
  return jsonify(VoiceStatusDto.from_status(_voice_processor().snapshot()).to_dict()), 200


@voice_blueprint.get("/events")
def events() -> Response:
  processor = _voice_processor()
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


def _encode_sse(event: VoiceEvent) -> str:
  return f"event: {event.event_type}\ndata: {json.dumps(_event_payload(event))}\n\n"


def _event_payload(event: VoiceEvent) -> dict[str, object]:
  payload = event.payload
  if isinstance(payload, VoiceStatus):
    return VoiceStatusDto.from_status(payload).to_dict()
  if isinstance(payload, TranscriptEntry):
    return TranscriptEntryDto.from_entry(payload).to_dict()
  if isinstance(payload, WakeWordEvent):
    return WakeWordEventDto.from_event(payload).to_dict()
  raise ValueError(f"Unsupported voice event type: {event.event_type}")
