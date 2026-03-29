from __future__ import annotations

import queue
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import pytest

from mobile_ingestion import create_app
from mobile_ingestion.analyzer import AnalyzerMetrics, AnalyzerPort, SessionMetadata
from mobile_ingestion.arduino import (ArduinoConflictError, ArduinoEvent,
                                      ArduinoSnapshot, ArduinoSubscription)
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.services import ServiceContainer
from mobile_ingestion.voice import (TranscriptEntry, VoiceEvent,
                                    VoiceStatus, VoiceSubscription,
                                    WakeWordEvent)
from uart_protocol import ActuatorCommand


class RecordingAnalyzer(AnalyzerPort):

  def __init__(self) -> None:
    self.metrics = AnalyzerMetrics()

  def on_session_started(self, metadata: SessionMetadata) -> None:
    del metadata
    self.metrics = AnalyzerMetrics(
        sessions_started=self.metrics.sessions_started + 1,
        sessions_stopped=self.metrics.sessions_stopped,
        video_frames=self.metrics.video_frames,
        audio_frames=self.metrics.audio_frames,
    )

  def on_video_frame(self, frame: object) -> None:
    del frame

  def on_audio_frame(self, frame: object) -> None:
    del frame

  def on_session_stopped(self, metadata: SessionMetadata) -> None:
    del metadata
    self.metrics = AnalyzerMetrics(
        sessions_started=self.metrics.sessions_started,
        sessions_stopped=self.metrics.sessions_stopped + 1,
        video_frames=self.metrics.video_frames,
        audio_frames=self.metrics.audio_frames,
    )

  def snapshot(self) -> AnalyzerMetrics:
    return self.metrics


@dataclass
class FakeStatus:
  active: bool = False
  state: str = "idle"
  connection_state: str = "closed"
  has_video_track: bool = False
  has_audio_track: bool = False
  error: str | None = None

  def to_dict(self) -> dict[str, object]:
    return {
        "state": self.state,
        "active": self.active,
        "sessionId": "fake-session" if self.active else None,
        "connectionState": self.connection_state,
        "hasVideoTrack": self.has_video_track,
        "hasAudioTrack": self.has_audio_track,
        "startedAt": None,
        "error": self.error,
        "analyzerMetrics": AnalyzerMetrics().to_dict(),
    }


class FakeSessionManager:

  def __init__(self) -> None:
    self.status = FakeStatus()
    self.closed = 0
    self.offers: list[SessionDescriptionDto] = []

  def accept_offer(self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    self.offers.append(offer)
    self.status = FakeStatus(
        active=True,
        state="streaming",
        connection_state="connected",
        has_video_track=True,
        has_audio_track=True,
        error=None,
    )
    return SessionDescriptionDto(sdp="answer-sdp", type="answer")

  def get_status(self) -> FakeStatus:
    return self.status

  def close_active_session(self) -> None:
    self.closed += 1
    self.status = FakeStatus()

  def shutdown(self) -> None:
    self.close_active_session()


def make_snapshot(**changes: object) -> ArduinoSnapshot:
  snapshot = ArduinoSnapshot(
      available=True,
      connected=False,
      keepalive_active=False,
      selected_port=None,
      baud_rate=115200,
      tx_count=0,
      rx_count=0,
      invalid_frame_count=0,
      detail="serial ready",
      debug_enabled=False,
      backend_command=ActuatorCommand(90.0, False),
      debug_command=ActuatorCommand(90.0, False),
      effective_command=ActuatorCommand(90.0, False),
      latest_telemetry=None,
      last_rx_timestamp=None,
      recent_frames=tuple(),
  )
  return replace(snapshot, **changes)


class FakeArduinoController:

  def __init__(self) -> None:
    self.snapshot = make_snapshot()
    self.closed = 0
    self.port_calls = 0
    self.last_port: str | None = None
    self.subscription_events: "queue.Queue[ArduinoEvent | None]" = queue.Queue()
    self.subscription_events.put(ArduinoEvent("status", self.snapshot))
    self.subscription_events.put(None)

  def list_ports(self) -> tuple[str, ...]:
    self.port_calls += 1
    return (
        "/dev/ttyUSB0",
        "/dev/ttyUSB1",
        "HC-05 [98:D3:11:FD:07:FF]",
    )

  def connect(self, port: str) -> None:
    self.last_port = port
    self.snapshot = replace(
        self.snapshot,
        connected=True,
        keepalive_active=True,
        selected_port=port,
        detail=f"connected to {port}",
    )

  def disconnect(self) -> None:
    self.closed += 1
    self.snapshot = replace(
        self.snapshot,
        connected=False,
        keepalive_active=False,
        detail="disconnected",
    )

  def shutdown(self) -> None:
    self.disconnect()

  def get_snapshot(self) -> ArduinoSnapshot:
    return self.snapshot

  def set_backend_command(self, command: ActuatorCommand) -> None:
    effective_command = (command if not self.snapshot.debug_enabled
                         else self.snapshot.debug_command)
    self.snapshot = replace(
        self.snapshot,
        backend_command=command,
        effective_command=effective_command,
    )

  def set_debug_enabled(self, enabled: bool) -> None:
    effective_command = (self.snapshot.debug_command
                         if enabled else self.snapshot.backend_command)
    self.snapshot = replace(
        self.snapshot,
        debug_enabled=enabled,
        effective_command=effective_command,
    )

  def set_debug_command(self, command: ActuatorCommand) -> None:
    if not self.snapshot.debug_enabled or not self.snapshot.connected:
      raise ArduinoConflictError("manual control unavailable")
    self.snapshot = replace(
        self.snapshot,
        debug_command=command,
        effective_command=command,
    )

  def subscribe(self) -> ArduinoSubscription:
    return ArduinoSubscription(1, self.subscription_events)

  def unsubscribe(self, subscription: ArduinoSubscription) -> None:
    del subscription


@dataclass(slots=True)
class FakeRuntime:

  def stop(self, timeout: float = 0.0) -> None:
    del timeout


class FakeVoiceProcessor:

  def __init__(self) -> None:
    received_at = datetime.now(timezone.utc)
    self.status = VoiceStatus(
        available=True,
        active=False,
        session_id=None,
        error=None,
        dropped_chunks=0,
        mode_state="idle",
        last_wake_word=WakeWordEvent(
            session_id="voice-session",
            phrase="okay jarvis",
            received_at=received_at,
            entry_id="entry-one",
        ),
        entries=(
            TranscriptEntry(
                entry_id="entry-one",
                session_id="voice-session",
                text="bonjour",
                is_final=True,
                received_at=received_at,
            ),
        ),
    )
    self.events: "queue.Queue[VoiceEvent | None]" = queue.Queue()
    self.events.put(VoiceEvent("status", self.status))
    self.events.put(None)

  def start_session(self, session_id: str) -> None:
    self.status = replace(self.status, session_id=session_id, active=True)

  def submit_audio(self, chunk: object) -> None:
    del chunk

  def stop_session(self, session_id: str) -> None:
    if self.status.session_id == session_id:
      self.status = replace(self.status, session_id=None, active=False)

  def snapshot(self) -> VoiceStatus:
    return self.status

  def subscribe(self) -> VoiceSubscription:
    return VoiceSubscription(1, self.events)

  def unsubscribe(self, subscription: VoiceSubscription) -> None:
    del subscription

  def shutdown(self) -> None:
    self.status = replace(self.status, active=False, session_id=None)


@pytest.fixture
def client():
  settings = AppConfig(testing=True)
  services = ServiceContainer(
      settings=settings,
      runtime=FakeRuntime(),
      analyzer=RecordingAnalyzer(),
      session_manager=FakeSessionManager(),
      arduino_controller=FakeArduinoController(),
      voice_processor=FakeVoiceProcessor(),
  )
  app = create_app(settings, services=services)
  app.config.update(TESTING=True)
  with app.test_client() as test_client:
    yield test_client


def test_index_renders(client) -> None:
  response = client.get("/")

  assert response.status_code == 200
  body = response.get_data(as_text=True)
  assert "Connexion mobile temps réel" in body
  assert "Debug Arduino" in body
  assert "Voice debug" in body
  assert "videoMaxFps" in body
  assert "Dernière transcription" in body


def test_status_route_returns_json(client) -> None:
  response = client.get("/api/webrtc/status")

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["state"] == "idle"
  assert payload["active"] is False


def test_offer_route_returns_answer(client) -> None:
  response = client.post("/api/webrtc/offer",
                         json={
                             "sdp": "offer-sdp",
                             "type": "offer",
                         })

  assert response.status_code == 200
  assert response.get_json() == {
      "sdp": "answer-sdp",
      "type": "answer",
  }


def test_delete_session_is_idempotent(client) -> None:
  first_response = client.delete("/api/webrtc/session")
  second_response = client.delete("/api/webrtc/session")

  assert first_response.status_code == 204
  assert second_response.status_code == 204


def test_arduino_status_route_returns_json(client) -> None:
  response = client.get("/api/arduino/status")

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["available"] is True
  assert payload["connected"] is False
  assert payload["debugEnabled"] is False


def test_voice_status_route_returns_json(client) -> None:
  response = client.get("/api/voice/status")

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["available"] is True
  assert payload["modeState"] == "idle"
  assert payload["entries"][0]["text"] == "bonjour"


def test_arduino_connect_route_returns_status_snapshot(client) -> None:
  response = client.post("/api/arduino/connection", json={"port": "/dev/ttyUSB0"})

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["connected"] is True
  assert payload["selectedPort"] == "/dev/ttyUSB0"


def test_arduino_ports_route_returns_serial_and_bluetooth_targets(client) -> None:
  response = client.get("/api/arduino/ports")

  assert response.status_code == 200
  assert response.get_json() == {
      "ports": [
          "/dev/ttyUSB0",
          "/dev/ttyUSB1",
          "HC-05 [98:D3:11:FD:07:FF]",
      ]
  }


def test_arduino_debug_command_conflict_returns_409(client) -> None:
  response = client.put(
      "/api/arduino/debug/command",
      json={
          "servoAngleDegrees": 45.0,
          "vibrationEnabled": True,
      },
  )

  assert response.status_code == 409
  assert response.get_json()["error"] == "manual control unavailable"


def test_arduino_command_route_updates_backend_command(client) -> None:
  response = client.put(
      "/api/arduino/command",
      json={
          "servoAngleDegrees": 45.0,
          "vibrationEnabled": True,
      },
  )

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["backendCommand"] == {
      "servoAngleDegrees": 45.0,
      "vibrationEnabled": True,
  }
  assert payload["effectiveCommand"] == {
      "servoAngleDegrees": 45.0,
      "vibrationEnabled": True,
  }


def test_arduino_events_route_streams_sse(client) -> None:
  response = client.get("/api/arduino/events")
  body = b"".join(response.response)

  assert response.status_code == 200
  assert b"event: status" in body
  assert b"debugEnabled" in body


def test_voice_events_route_streams_sse(client) -> None:
  response = client.get("/api/voice/events")
  body = b"".join(response.response)

  assert response.status_code == 200
  assert b"event: status" in body
  assert b"modeState" in body
