from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Generator

import pytest

from mobile_ingestion.analyzer import (AnalyzerMetrics, AnalyzerPort,
                                       AudioFrameEnvelope, SessionMetadata,
                                       VideoFrameEnvelope)
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto, SessionOfferRequestDto
from mobile_ingestion.mode_manager import RuntimeModeStatus
from mobile_ingestion.object_search import ObjectSearchStatus
from mobile_ingestion.runtime import AsyncioRunner
from mobile_ingestion.session_manager import (SessionBusyError,
                                              SessionCallbacks,
                                              SessionContext,
                                              SessionManager,
                                              SessionUnavailableError)
from mobile_ingestion.voice import VoiceStatus


class RecordingAnalyzer(AnalyzerPort):

  def __init__(self) -> None:
    self.started_sessions: list[str] = []
    self.stopped_sessions: list[str] = []
    self.video_frames = 0
    self.audio_frames = 0

  def on_session_started(self, metadata: SessionMetadata) -> None:
    self.started_sessions.append(metadata.session_id)

  def on_video_frame(self, frame: VideoFrameEnvelope) -> None:
    del frame
    self.video_frames += 1

  def on_audio_frame(self, frame: AudioFrameEnvelope) -> None:
    del frame
    self.audio_frames += 1

  def on_session_stopped(self, metadata: SessionMetadata) -> None:
    self.stopped_sessions.append(metadata.session_id)

  def snapshot(self) -> AnalyzerMetrics:
    return AnalyzerMetrics(
        sessions_started=len(self.started_sessions),
        sessions_stopped=len(self.stopped_sessions),
        video_frames=self.video_frames,
        audio_frames=self.audio_frames,
    )


class RecordingVoiceProcessor:

  def __init__(self) -> None:
    self.started_sessions: list[str] = []
    self.stopped_sessions: list[str] = []
    self.status = VoiceStatus(
        available=True,
        active=False,
        session_id=None,
        error=None,
    )

  def start_session(self, session_id: str) -> None:
    self.started_sessions.append(session_id)
    self.status = VoiceStatus(
        available=True,
        active=True,
        session_id=session_id,
        error=None,
    )

  def submit_audio(self, chunk: object) -> None:
    del chunk

  def stop_session(self, session_id: str) -> None:
    self.stopped_sessions.append(session_id)
    if self.status.session_id == session_id:
      self.status = VoiceStatus(
          available=True,
          active=False,
          session_id=None,
          error=None,
      )

  def snapshot(self) -> VoiceStatus:
    return self.status

  def subscribe(self) -> object:
    raise NotImplementedError

  def unsubscribe(self, subscription: object) -> None:
    del subscription
    raise NotImplementedError

  def shutdown(self) -> None:
    self.status = VoiceStatus(
        available=True,
        active=False,
        session_id=None,
        error=None,
    )


class RecordingObjectSearch:

  def __init__(self) -> None:
    self.started_sessions: list[str] = []
    self.stopped_sessions: list[str] = []
    self.status = ObjectSearchStatus(
        available=True,
        active=False,
        session_id=None,
        state="idle",
        target_label=None,
        detected=False,
        last_detected_at=None,
        error=None,
    )

  def start_session(self, session_id: str) -> None:
    self.started_sessions.append(session_id)
    self.status = ObjectSearchStatus(
        available=True,
        active=True,
        session_id=session_id,
        state="idle",
        target_label=None,
        detected=False,
        last_detected_at=None,
        error=None,
    )

  def submit_frame(self, frame: object) -> None:
    del frame

  def stop_session(self, session_id: str) -> None:
    self.stopped_sessions.append(session_id)
    if self.status.session_id == session_id:
      self.status = ObjectSearchStatus(
          available=True,
          active=False,
          session_id=None,
          state="idle",
          target_label=None,
          detected=False,
          last_detected_at=None,
          error=None,
      )

  def snapshot(self) -> ObjectSearchStatus:
    return self.status

  def subscribe(self) -> object:
    raise NotImplementedError

  def unsubscribe(self, subscription: object) -> None:
    del subscription
    raise NotImplementedError

  def shutdown(self) -> None:
    self.status = ObjectSearchStatus(
        available=True,
        active=False,
        session_id=None,
        state="idle",
        target_label=None,
        detected=False,
        last_detected_at=None,
        error=None,
    )


class RecordingRuntimeMode:

  def __init__(self) -> None:
    self.started_sessions: list[str] = []
    self.stopped_sessions: list[str] = []
    self.status = RuntimeModeStatus(
        available=True,
        active=False,
        session_id=None,
        mode="idle",
        detail="Mode idle.",
        error=None,
    )

  def start_session(self, session_id: str) -> None:
    self.started_sessions.append(session_id)
    self.status = RuntimeModeStatus(
        available=True,
        active=True,
        session_id=session_id,
        mode="idle",
        detail="Mode idle.",
        error=None,
    )

  def submit_frame(self, frame: object) -> None:
    del frame

  def stop_session(self, session_id: str) -> None:
    self.stopped_sessions.append(session_id)
    if self.status.session_id == session_id:
      self.status = RuntimeModeStatus(
          available=True,
          active=False,
          session_id=None,
          mode="idle",
          detail="Mode idle.",
          error=None,
      )

  def snapshot(self) -> RuntimeModeStatus:
    return self.status

  def subscribe(self) -> object:
    raise NotImplementedError

  def unsubscribe(self, subscription: object) -> None:
    del subscription
    raise NotImplementedError

  def shutdown(self) -> None:
    self.status = RuntimeModeStatus(
        available=True,
        active=False,
        session_id=None,
        mode="idle",
        detail="Mode idle.",
        error=None,
    )


@dataclass
class FakePeerSession:
  context: SessionContext
  callbacks: SessionCallbacks
  answered: bool = False
  closed: bool = False

  async def accept_offer(
      self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    del offer
    self.callbacks.on_connection_state_changed("connected")
    self.callbacks.on_video_track_detected()
    if self.context.role == "sender":
      self.context.analyzer.on_session_started(
          SessionMetadata(
              session_id=self.context.session_id,
              started_at=self.context.started_at,
          ))
      self.callbacks.on_audio_track_detected()
      self.context.analyzer.on_video_frame(
          VideoFrameEnvelope(
              session_id=self.context.session_id,
              received_at=self.context.started_at,
              width=1280,
              height=720,
              pts=1,
          ))
      self.context.analyzer.on_audio_frame(
          AudioFrameEnvelope(
              session_id=self.context.session_id,
              received_at=self.context.started_at,
              sample_rate=48000,
              samples=960,
              pts=1,
          ))
    self.answered = True
    return SessionDescriptionDto(sdp=f"answer:{self.context.role}", type="answer")

  async def close(self) -> None:
    if self.closed:
      return
    self.closed = True
    if self.context.role == "sender":
      self.context.analyzer.on_session_stopped(
          SessionMetadata(
              session_id=self.context.session_id,
              started_at=self.context.started_at,
          ))
    self.callbacks.on_closed()


@pytest.fixture
def manager(
) -> Generator[tuple[SessionManager, RecordingAnalyzer, RecordingVoiceProcessor,
                     RecordingObjectSearch, RecordingRuntimeMode], None, None]:
  runtime = AsyncioRunner()
  analyzer = RecordingAnalyzer()
  voice_processor = RecordingVoiceProcessor()
  object_search = RecordingObjectSearch()
  runtime_mode = RecordingRuntimeMode()
  settings = AppConfig(testing=True)
  session_manager = SessionManager(
      runtime=runtime,
      analyzer=analyzer,
      voice_processor=voice_processor,
      object_search=object_search,
      runtime_mode=runtime_mode,
      settings=settings,
      session_factory=lambda context, callbacks: FakePeerSession(
          context=context,
          callbacks=callbacks,
      ),
  )
  try:
    yield session_manager, analyzer, voice_processor, object_search, runtime_mode
  finally:
    session_manager.shutdown()


def wait_until(predicate, timeout_seconds: float = 2.0) -> None:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.01)
  raise AssertionError("condition not met before timeout")


def test_session_manager_accepts_sender_offer_and_updates_status(manager) -> None:
  session_manager, analyzer, voice_processor, object_search, runtime_mode = manager

  answer = session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="sender"))
  status = session_manager.get_status()

  assert answer.type == "answer"
  assert answer.role == "sender"
  assert answer.session_token
  assert status.room_state == "sender_streaming"
  assert status.sender_occupied is True
  assert status.spectator_occupied is False
  assert status.sender_video_available is True
  assert status.sender is not None
  assert status.sender.has_audio_track is True
  assert analyzer.snapshot().video_frames == 1
  assert analyzer.snapshot().audio_frames == 1
  assert len(voice_processor.started_sessions) == 1
  assert len(object_search.started_sessions) == 1
  assert len(runtime_mode.started_sessions) == 1


def test_session_manager_rejects_second_sender_session(manager) -> None:
  session_manager, _, _, _, _ = manager

  session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="sender"))

  with pytest.raises(SessionBusyError):
    session_manager.accept_offer(
        SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="sender"))


def test_session_manager_rejects_spectator_without_sender_video(manager) -> None:
  session_manager, _, _, _, _ = manager

  with pytest.raises(SessionUnavailableError):
    session_manager.accept_offer(
        SessionOfferRequestDto(
            sdp="offer-sdp",
            type="offer",
            role="spectator",
        ))


def test_session_manager_accepts_spectator_after_sender(manager) -> None:
  session_manager, _, _, _, _ = manager

  session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="sender"))
  spectator_answer = session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="spectator"))
  status = session_manager.get_status()

  assert spectator_answer.role == "spectator"
  assert spectator_answer.session_token
  assert status.room_state == "full"
  assert status.sender_occupied is True
  assert status.spectator_occupied is True
  assert status.spectator is not None
  assert status.spectator.has_video_track is True
  assert status.spectator.has_audio_track is False


def test_closing_sender_session_closes_spectator_and_resets_room(manager) -> None:
  session_manager, analyzer, voice_processor, object_search, runtime_mode = manager

  sender_answer = session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="sender"))
  spectator_answer = session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="spectator"))

  session_manager.close_session(sender_answer.session_token)
  wait_until(lambda: session_manager.get_status().sender_occupied is False)
  wait_until(lambda: session_manager.get_status().spectator_occupied is False)

  status = session_manager.get_status()
  assert spectator_answer.session_token
  assert status.room_state == "idle"
  assert analyzer.snapshot().sessions_stopped == 1
  assert voice_processor.status.active is False
  assert object_search.status.active is False
  assert runtime_mode.status.active is False


def test_closing_spectator_keeps_sender_running(manager) -> None:
  session_manager, _, voice_processor, object_search, runtime_mode = manager

  session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="sender"))
  spectator_answer = session_manager.accept_offer(
      SessionOfferRequestDto(sdp="offer-sdp", type="offer", role="spectator"))

  session_manager.close_session(spectator_answer.session_token)
  wait_until(lambda: session_manager.get_status().spectator_occupied is False)
  status = session_manager.get_status()

  assert status.room_state == "sender_streaming"
  assert status.sender_occupied is True
  assert status.spectator_occupied is False
  assert voice_processor.status.active is True
  assert object_search.status.active is True
  assert runtime_mode.status.active is True
