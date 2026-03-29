from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from mobile_ingestion.analyzer import AnalyzerMetrics
from mobile_ingestion.object_search import ObjectSearchStatus
from mobile_ingestion.arduino import ArduinoSnapshot
from mobile_ingestion.voice import TranscriptEntry, VoiceStatus, WakeWordEvent
from uart_protocol import ActuatorCommand, RawFrameEvent, TelemetrySnapshot


@dataclass(frozen=True, slots=True)
class SessionDescriptionDto:
  sdp: str
  type: str

  @classmethod
  def from_mapping(cls,
                   payload: Mapping[str, Any]) -> "SessionDescriptionDto":
    sdp = payload.get("sdp")
    kind = payload.get("type")
    if not isinstance(sdp, str) or not sdp.strip():
      raise ValueError("Field 'sdp' must be a non-empty string.")
    if not isinstance(kind, str) or kind not in {"offer", "answer"}:
      raise ValueError("Field 'type' must be either 'offer' or 'answer'.")
    return cls(sdp=sdp, type=kind)

  def to_dict(self) -> dict[str, str]:
    return {
        "sdp": self.sdp,
        "type": self.type,
    }


@dataclass(frozen=True, slots=True)
class SessionStatusDto:
  state: str
  active: bool
  session_id: str | None
  connection_state: str
  has_video_track: bool
  has_audio_track: bool
  started_at: str | None
  error: str | None
  analyzer_metrics: AnalyzerMetrics

  @classmethod
  def from_values(
      cls,
      *,
      state: str,
      active: bool,
      session_id: str | None,
      connection_state: str,
      has_video_track: bool,
      has_audio_track: bool,
      started_at: datetime | None,
      error: str | None,
      analyzer_metrics: AnalyzerMetrics,
  ) -> "SessionStatusDto":
    return cls(
        state=state,
        active=active,
        session_id=session_id,
        connection_state=connection_state,
        has_video_track=has_video_track,
        has_audio_track=has_audio_track,
        started_at=started_at.isoformat() if started_at else None,
        error=error,
        analyzer_metrics=analyzer_metrics,
    )

  def to_dict(self) -> dict[str, Any]:
    return {
        "state": self.state,
        "active": self.active,
        "sessionId": self.session_id,
        "connectionState": self.connection_state,
        "hasVideoTrack": self.has_video_track,
        "hasAudioTrack": self.has_audio_track,
        "startedAt": self.started_at,
        "error": self.error,
        "analyzerMetrics": self.analyzer_metrics.to_dict(),
    }


def _require_boolean(payload: Mapping[str, Any], field_name: str) -> bool:
  value = payload.get(field_name)
  if not isinstance(value, bool):
    raise ValueError(f"Field '{field_name}' must be a boolean.")
  return value


@dataclass(frozen=True, slots=True)
class ArduinoConnectRequestDto:
  port: str

  @classmethod
  def from_mapping(cls,
                   payload: Mapping[str, Any]) -> "ArduinoConnectRequestDto":
    port = payload.get("port")
    if not isinstance(port, str) or not port.strip():
      raise ValueError("Field 'port' must be a non-empty string.")
    return cls(port=port.strip())


@dataclass(frozen=True, slots=True)
class ArduinoDebugModeDto:
  enabled: bool

  @classmethod
  def from_mapping(cls, payload: Mapping[str, Any]) -> "ArduinoDebugModeDto":
    return cls(enabled=_require_boolean(payload, "enabled"))


@dataclass(frozen=True, slots=True)
class ArduinoCommandDto:
  servo_angle_degrees: float
  vibration_enabled: bool

  @classmethod
  def from_mapping(cls, payload: Mapping[str, Any]) -> "ArduinoCommandDto":
    raw_servo_angle = payload.get("servoAngleDegrees")
    if not isinstance(raw_servo_angle, (int, float)):
      raise ValueError("Field 'servoAngleDegrees' must be a number.")
    return cls(
        servo_angle_degrees=float(raw_servo_angle),
        vibration_enabled=_require_boolean(payload, "vibrationEnabled"),
    )

  @classmethod
  def from_command(cls, command: ActuatorCommand) -> "ArduinoCommandDto":
    return cls(
        servo_angle_degrees=command.servo_angle_degrees,
        vibration_enabled=command.vibration_enabled,
    )

  def to_command(self) -> ActuatorCommand:
    return ActuatorCommand(
        servo_angle_degrees=self.servo_angle_degrees,
        vibration_enabled=self.vibration_enabled,
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "servoAngleDegrees": self.servo_angle_degrees,
        "vibrationEnabled": self.vibration_enabled,
    }


@dataclass(frozen=True, slots=True)
class TelemetrySnapshotDto:
  distance_mm: int
  distance_valid: bool
  distance_timed_out: bool
  accel_x_mg: int
  accel_y_mg: int
  accel_z_mg: int
  accel_valid: bool
  joystick_x_permille: int
  joystick_y_permille: int
  joystick_button_pressed: bool

  @classmethod
  def from_snapshot(cls,
                    snapshot: TelemetrySnapshot) -> "TelemetrySnapshotDto":
    return cls(
        distance_mm=snapshot.distance_mm,
        distance_valid=snapshot.distance_valid,
        distance_timed_out=snapshot.distance_timed_out,
        accel_x_mg=snapshot.accel_x_mg,
        accel_y_mg=snapshot.accel_y_mg,
        accel_z_mg=snapshot.accel_z_mg,
        accel_valid=snapshot.accel_valid,
        joystick_x_permille=snapshot.joystick_x_permille,
        joystick_y_permille=snapshot.joystick_y_permille,
        joystick_button_pressed=snapshot.joystick_button_pressed,
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "distanceMm": self.distance_mm,
        "distanceValid": self.distance_valid,
        "distanceTimedOut": self.distance_timed_out,
        "accelXMg": self.accel_x_mg,
        "accelYMg": self.accel_y_mg,
        "accelZMg": self.accel_z_mg,
        "accelValid": self.accel_valid,
        "joystickXPermille": self.joystick_x_permille,
        "joystickYPermille": self.joystick_y_permille,
        "joystickButtonPressed": self.joystick_button_pressed,
    }


@dataclass(frozen=True, slots=True)
class RawFrameEventDto:
  direction: str
  message_type: int | None
  sequence: int | None
  hex_string: str
  timestamp: float
  status: str

  @classmethod
  def from_event(cls, event: RawFrameEvent) -> "RawFrameEventDto":
    return cls(
        direction=event.direction,
        message_type=event.message_type,
        sequence=event.sequence,
        hex_string=event.hex_string,
        timestamp=event.timestamp,
        status=event.status,
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "direction": self.direction,
        "messageType": self.message_type,
        "sequence": self.sequence,
        "hexString": self.hex_string,
        "timestamp": self.timestamp,
        "status": self.status,
    }


@dataclass(frozen=True, slots=True)
class TranscriptEntryDto:
  entry_id: str
  session_id: str
  text: str
  is_final: bool
  received_at: str

  @classmethod
  def from_entry(cls, entry: TranscriptEntry) -> "TranscriptEntryDto":
    return cls(
        entry_id=entry.entry_id,
        session_id=entry.session_id,
        text=entry.text,
        is_final=entry.is_final,
        received_at=entry.received_at.isoformat(),
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "entryId": self.entry_id,
        "sessionId": self.session_id,
        "text": self.text,
        "isFinal": self.is_final,
        "receivedAt": self.received_at,
    }


@dataclass(frozen=True, slots=True)
class WakeWordEventDto:
  session_id: str
  phrase: str
  received_at: str
  entry_id: str

  @classmethod
  def from_event(cls, event: WakeWordEvent) -> "WakeWordEventDto":
    return cls(
        session_id=event.session_id,
        phrase=event.phrase,
        received_at=event.received_at.isoformat(),
        entry_id=event.entry_id,
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "sessionId": self.session_id,
        "phrase": self.phrase,
        "receivedAt": self.received_at,
        "entryId": self.entry_id,
    }


@dataclass(frozen=True, slots=True)
class VoiceStatusDto:
  available: bool
  active: bool
  session_id: str | None
  error: str | None
  dropped_chunks: int
  mode_state: str
  last_wake_word: WakeWordEventDto | None
  entries: tuple[TranscriptEntryDto, ...]

  @classmethod
  def from_status(cls, status: VoiceStatus) -> "VoiceStatusDto":
    return cls(
        available=status.available,
        active=status.active,
        session_id=status.session_id,
        error=status.error,
        dropped_chunks=status.dropped_chunks,
        mode_state=status.mode_state,
        last_wake_word=(WakeWordEventDto.from_event(status.last_wake_word)
                        if status.last_wake_word is not None else None),
        entries=tuple(TranscriptEntryDto.from_entry(entry)
                      for entry in status.entries),
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "available": self.available,
        "active": self.active,
        "sessionId": self.session_id,
        "error": self.error,
        "droppedChunks": self.dropped_chunks,
        "modeState": self.mode_state,
        "lastWakeWord": (self.last_wake_word.to_dict()
                          if self.last_wake_word is not None else None),
        "entries": [entry.to_dict() for entry in self.entries],
    }


@dataclass(frozen=True, slots=True)
class ObjectSearchStatusDto:
  available: bool
  active: bool
  session_id: str | None
  state: str
  target_label: str | None
  detected: bool
  last_detected_at: str | None
  error: str | None
  model_ready: bool
  model_state: str
  model_detail: str | None
  selected_vision_model: str | None

  @classmethod
  def from_status(cls, status: ObjectSearchStatus) -> "ObjectSearchStatusDto":
    return cls(
        available=status.available,
        active=status.active,
        session_id=status.session_id,
        state=status.state,
        target_label=status.target_label,
        detected=status.detected,
        last_detected_at=(status.last_detected_at.isoformat()
                          if status.last_detected_at is not None else None),
        error=status.error,
        model_ready=status.model_ready,
        model_state=status.model_state,
        model_detail=status.model_detail,
        selected_vision_model=status.selected_vision_model,
    )

  def to_dict(self) -> dict[str, object]:
    return {
        "available": self.available,
        "active": self.active,
        "sessionId": self.session_id,
        "state": self.state,
        "targetLabel": self.target_label,
        "detected": self.detected,
        "lastDetectedAt": self.last_detected_at,
        "error": self.error,
        "modelReady": self.model_ready,
        "modelState": self.model_state,
        "modelDetail": self.model_detail,
        "selectedVisionModel": self.selected_vision_model,
    }


@dataclass(frozen=True, slots=True)
class ArduinoStatusDto:
  available: bool
  connected: bool
  keepalive_active: bool
  selected_port: str | None
  baud_rate: int
  tx_count: int
  rx_count: int
  invalid_frame_count: int
  detail: str
  debug_enabled: bool
  backend_command: ArduinoCommandDto
  debug_command: ArduinoCommandDto
  effective_command: ArduinoCommandDto
  latest_telemetry: TelemetrySnapshotDto | None
  last_rx_timestamp: float | None
  recent_frames: tuple[RawFrameEventDto, ...]

  @classmethod
  def from_snapshot(cls, snapshot: ArduinoSnapshot) -> "ArduinoStatusDto":
    latest_telemetry = None
    if snapshot.latest_telemetry is not None:
      latest_telemetry = TelemetrySnapshotDto.from_snapshot(
          snapshot.latest_telemetry)
    return cls(
        available=snapshot.available,
        connected=snapshot.connected,
        keepalive_active=snapshot.keepalive_active,
        selected_port=snapshot.selected_port,
        baud_rate=snapshot.baud_rate,
        tx_count=snapshot.tx_count,
        rx_count=snapshot.rx_count,
        invalid_frame_count=snapshot.invalid_frame_count,
        detail=snapshot.detail,
        debug_enabled=snapshot.debug_enabled,
        backend_command=ArduinoCommandDto.from_command(snapshot.backend_command),
        debug_command=ArduinoCommandDto.from_command(snapshot.debug_command),
        effective_command=ArduinoCommandDto.from_command(
            snapshot.effective_command),
        latest_telemetry=latest_telemetry,
        last_rx_timestamp=snapshot.last_rx_timestamp,
        recent_frames=tuple(
            RawFrameEventDto.from_event(event)
            for event in snapshot.recent_frames),
    )

  def to_dict(self, *, include_recent_frames: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "available": self.available,
        "connected": self.connected,
        "keepaliveActive": self.keepalive_active,
        "selectedPort": self.selected_port,
        "baudRate": self.baud_rate,
        "txCount": self.tx_count,
        "rxCount": self.rx_count,
        "invalidFrameCount": self.invalid_frame_count,
        "detail": self.detail,
        "debugEnabled": self.debug_enabled,
        "backendCommand": self.backend_command.to_dict(),
        "debugCommand": self.debug_command.to_dict(),
        "effectiveCommand": self.effective_command.to_dict(),
        "latestTelemetry": (self.latest_telemetry.to_dict()
                             if self.latest_telemetry is not None else None),
        "lastRxTimestamp": self.last_rx_timestamp,
    }
    if include_recent_frames:
      payload["recentFrames"] = [frame.to_dict() for frame in self.recent_frames]
    return payload
