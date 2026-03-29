from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from mobile_ingestion.arduino import (ArduinoConflictError, ArduinoControllerPort,
                                      ArduinoEvent, ArduinoUnavailableError)
from mobile_ingestion.dto import (ArduinoCommandDto, ArduinoConnectRequestDto,
                                  ArduinoDebugModeDto, ArduinoStatusDto,
                                  RawFrameEventDto, TelemetrySnapshotDto)


arduino_blueprint = Blueprint("arduino", __name__, url_prefix="/api/arduino")


def _controller() -> ArduinoControllerPort:
  services = current_app.extensions["mobile_ingestion.services"]
  return services.arduino_controller


@arduino_blueprint.get("/status")
def status() -> tuple[dict[str, object], int]:
  payload = ArduinoStatusDto.from_snapshot(
      _controller().get_snapshot()).to_dict()
  return jsonify(payload), 200


@arduino_blueprint.get("/ports")
def ports() -> tuple[dict[str, object], int]:
  return jsonify({"ports": list(_controller().list_ports())}), 200


@arduino_blueprint.post("/connection")
def connect() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  try:
    request_dto = ArduinoConnectRequestDto.from_mapping(payload)
    _controller().connect(request_dto.port)
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400
  except ArduinoConflictError as exc:
    return jsonify({"error": str(exc)}), 409
  except ArduinoUnavailableError as exc:
    return jsonify({"error": str(exc)}), 503
  except RuntimeError as exc:
    current_app.logger.exception("Failed to open Arduino connection.")
    return jsonify({"error": str(exc)}), 500

  return status()


@arduino_blueprint.delete("/connection")
def disconnect() -> tuple[dict[str, object], int]:
  try:
    _controller().disconnect()
  except RuntimeError as exc:
    current_app.logger.exception("Failed to close Arduino connection.")
    return jsonify({"error": str(exc)}), 500
  return status()


@arduino_blueprint.put("/debug")
def set_debug_mode() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  try:
    request_dto = ArduinoDebugModeDto.from_mapping(payload)
    _controller().set_debug_enabled(request_dto.enabled)
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400

  return status()


@arduino_blueprint.put("/debug/command")
def set_debug_command() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  try:
    request_dto = ArduinoCommandDto.from_mapping(payload)
    _controller().set_debug_command(request_dto.to_command())
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400
  except ArduinoConflictError as exc:
    return jsonify({"error": str(exc)}), 409

  return status()


@arduino_blueprint.put("/command")
def set_backend_command() -> tuple[dict[str, object], int]:
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"error": "Request body must be a JSON object."}), 400

  try:
    request_dto = ArduinoCommandDto.from_mapping(payload)
    _controller().set_backend_command(request_dto.to_command())
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400

  return status()


@arduino_blueprint.get("/events")
def events() -> Response:
  controller = _controller()
  subscription = controller.subscribe()

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
      controller.unsubscribe(subscription)

  return Response(
      stream_with_context(generate()),
      mimetype="text/event-stream",
      headers={
          "Cache-Control": "no-cache",
          "X-Accel-Buffering": "no",
      },
  )


def _encode_sse(event: ArduinoEvent) -> str:
  return f"event: {event.event_type}\ndata: {json.dumps(_event_payload(event))}\n\n"


def _event_payload(event: ArduinoEvent) -> dict[str, object]:
  if event.event_type == "status":
    return ArduinoStatusDto.from_snapshot(
        event.payload).to_dict(include_recent_frames=False)
  if event.event_type == "telemetry":
    return TelemetrySnapshotDto.from_snapshot(event.payload).to_dict()
  if event.event_type == "frame":
    return RawFrameEventDto.from_event(event.payload).to_dict()
  raise ValueError(f"Unsupported Arduino event type: {event.event_type}")
