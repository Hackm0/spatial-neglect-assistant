from __future__ import annotations

from dataclasses import dataclass

import pytest

from mobile_ingestion import create_app
from mobile_ingestion.analyzer import AnalyzerMetrics, AnalyzerPort, SessionMetadata
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.services import ServiceContainer


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
