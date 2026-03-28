from __future__ import annotations

from dataclasses import dataclass

import pytest

from mobile_ingestion import create_app
from mobile_ingestion.analyzer import (AnalyzerMetrics, AnalyzerPort,
                                       SessionMetadata, TranscriptEntry,
                                       TranscriptSnapshot)
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.services import ServiceContainer


class RecordingAnalyzer(AnalyzerPort):

  def __init__(self) -> None:
    self.metrics = AnalyzerMetrics()
    self.transcript_entries: list[TranscriptEntry] = []

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

  def on_transcript(self, text: str, *, final: bool, source: str) -> None:
    self.transcript_entries.append(
        TranscriptEntry(
            index=len(self.transcript_entries),
            text=text,
            final=final,
            source=source,
            created_at="test",
        ))

  def transcript_snapshot(self) -> TranscriptSnapshot:
    return TranscriptSnapshot(entries=tuple(self.transcript_entries))

  def clear_transcript(self) -> None:
    self.transcript_entries.clear()


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


@dataclass(slots=True)
class FakeRuntime:

  def stop(self, timeout: float = 0.0) -> None:
    del timeout


@pytest.fixture
def client():
  settings = AppConfig(testing=True)
  services = ServiceContainer(
      settings=settings,
      runtime=FakeRuntime(),
      analyzer=RecordingAnalyzer(),
      session_manager=FakeSessionManager(),
  )
  app = create_app(settings, services=services)
  app.config.update(TESTING=True)
  with app.test_client() as test_client:
    yield test_client


def test_index_renders(client) -> None:
  response = client.get("/")

  assert response.status_code == 200
  assert "Connexion mobile temps réel" in response.get_data(as_text=True)


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


def test_command_route_supports_status_alias(client) -> None:
  response = client.post("/api/webrtc/command", json={"action": "state"})

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["command"] == "status"
  assert payload["result"]["state"] == "idle"


def test_command_route_supports_nested_offer_payload(client) -> None:
  response = client.post(
      "/api/webrtc/command",
      json={
          "command": "connect",
          "payload": {
              "sdp": "offer-sdp",
              "type": "offer",
          },
      },
  )

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["command"] == "offer"
  assert payload["result"] == {
      "sdp": "answer-sdp",
      "type": "answer",
  }


def test_command_route_supports_close_alias(client) -> None:
  response = client.post("/api/webrtc/command", json={"event": "disconnect"})

  assert response.status_code == 200
  payload = response.get_json()
  assert payload["command"] == "close_session"
  assert payload["result"]["state"] == "idle"


def test_command_route_returns_unknown_for_unhandled_command(client) -> None:
  response = client.post("/api/webrtc/command", json={"command": "do-magic"})

  assert response.status_code == 422
  payload = response.get_json()
  assert payload["command"] == "unknown"
  assert payload["rawCommand"] == "do-magic"


def test_transcript_crud_routes(client) -> None:
  add_response = client.post(
      "/api/webrtc/transcript",
      json={
          "text": "patient says hello",
          "final": True,
          "source": "asr",
      },
  )
  assert add_response.status_code == 200
  added_payload = add_response.get_json()
  assert added_payload["count"] == 1
  assert added_payload["entries"][0]["text"] == "patient says hello"

  get_response = client.get("/api/webrtc/transcript")
  assert get_response.status_code == 200
  get_payload = get_response.get_json()
  assert get_payload["count"] == 1

  clear_response = client.delete("/api/webrtc/transcript")
  assert clear_response.status_code == 204

  empty_response = client.get("/api/webrtc/transcript")
  assert empty_response.status_code == 200
  assert empty_response.get_json()["count"] == 0


def test_command_route_supports_transcript_commands(client) -> None:
  add_response = client.post(
      "/api/webrtc/command",
      json={
          "command": "caption",
          "payload": {
              "text": "turn left",
              "final": False,
              "source": "speech",
          },
      },
  )
  assert add_response.status_code == 200
  add_payload = add_response.get_json()
  assert add_payload["command"] == "transcript_add"
  assert add_payload["result"]["count"] == 1

  get_response = client.post("/api/webrtc/command", json={"command": "transcript"})
  assert get_response.status_code == 200
  get_payload = get_response.get_json()
  assert get_payload["command"] == "transcript_get"
  assert get_payload["result"]["count"] == 1

  clear_response = client.post(
      "/api/webrtc/command",
      json={"action": "clear_transcript"},
  )
  assert clear_response.status_code == 200
  clear_payload = clear_response.get_json()
  assert clear_payload["command"] == "transcript_clear"
  assert clear_payload["result"]["count"] == 0


def test_command_route_transcript_end_to_end_flow(client) -> None:
  first_add = client.post(
      "/api/webrtc/command",
      json={
          "command": "transcript_add",
          "payload": {
              "text": "patient ready",
              "final": True,
              "source": "speech-v1",
          },
      },
  )
  assert first_add.status_code == 200

  second_add = client.post(
      "/api/webrtc/command",
      json={
          "command": "transcribe",
          "payload": {
              "text": "move to the right",
              "final": False,
              "source": "speech-v1",
          },
      },
  )
  assert second_add.status_code == 200

  get_response = client.post(
      "/api/webrtc/command",
      json={"command": "get_transcript"},
  )
  assert get_response.status_code == 200
  transcript_payload = get_response.get_json()
  assert transcript_payload["command"] == "transcript_get"
  assert transcript_payload["result"]["count"] == 2
  assert transcript_payload["result"]["entries"][0]["text"] == "patient ready"
  assert transcript_payload["result"]["entries"][0]["final"] is True
  assert transcript_payload["result"]["entries"][1]["text"] == "move to the right"
  assert transcript_payload["result"]["entries"][1]["final"] is False

  clear_response = client.post(
      "/api/webrtc/command",
      json={"action": "reset_transcript"},
  )
  assert clear_response.status_code == 200
  assert clear_response.get_json()["result"]["count"] == 0

  get_after_clear = client.post(
      "/api/webrtc/command",
      json={"event": "transcript"},
  )
  assert get_after_clear.status_code == 200
  assert get_after_clear.get_json()["result"]["count"] == 0
