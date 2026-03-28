from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import pytest

from mobile_ingestion.analyzer import (AnalyzerMetrics, AnalyzerPort,
                                       AudioFrameEnvelope, SessionMetadata,
                                       TranscriptEntry, TranscriptSnapshot,
                                       VideoFrameEnvelope)
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto
from mobile_ingestion.runtime import AsyncioRunner
from mobile_ingestion.session_manager import (SessionBusyError, SessionCallbacks,
                                              SessionContext, SessionManager)


class RecordingAnalyzer(AnalyzerPort):

  def __init__(self) -> None:
    self.started_sessions: list[str] = []
    self.stopped_sessions: list[str] = []
    self.video_frames = 0
    self.audio_frames = 0
    self.transcript_entries: list[TranscriptEntry] = []

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
class FakePeerSession:
  context: SessionContext
  callbacks: SessionCallbacks
  answered: bool = False
  closed: bool = False

  async def accept_offer(
      self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    self.context.analyzer.on_session_started(
        SessionMetadata(
            session_id=self.context.session_id,
            started_at=self.context.started_at,
        ))
    self.callbacks.on_connection_state_changed("connected")
    self.callbacks.on_video_track_detected()
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
    return SessionDescriptionDto(sdp=f"answer:{offer.sdp}", type="answer")

  async def close(self) -> None:
    if self.closed:
      return
    self.closed = True
    self.context.analyzer.on_session_stopped(
        SessionMetadata(
            session_id=self.context.session_id,
            started_at=self.context.started_at,
        ))
    self.callbacks.on_closed()


@pytest.fixture
def runtime() -> AsyncioRunner:
  runner = AsyncioRunner()
  yield runner
  runner.stop()


@pytest.fixture
def manager(runtime: AsyncioRunner) -> tuple[SessionManager, RecordingAnalyzer]:
  analyzer = RecordingAnalyzer()
  settings = AppConfig(testing=True)
  session_manager = SessionManager(
      runtime=runtime,
      analyzer=analyzer,
      settings=settings,
      session_factory=lambda context, callbacks: FakePeerSession(
          context=context,
          callbacks=callbacks,
      ),
  )
  return session_manager, analyzer


def test_session_manager_accepts_offer_and_updates_status(manager) -> None:
  session_manager, analyzer = manager

  answer = session_manager.accept_offer(
      SessionDescriptionDto(sdp="offer-sdp", type="offer"))
  status = session_manager.get_status()

  assert answer.type == "answer"
  assert answer.sdp == "answer:offer-sdp"
  assert status.state == "streaming"
  assert status.active is True
  assert status.connection_state == "connected"
  assert status.has_video_track is True
  assert status.has_audio_track is True
  assert analyzer.snapshot().sessions_started == 1
  assert analyzer.snapshot().video_frames == 1
  assert analyzer.snapshot().audio_frames == 1


def test_session_manager_rejects_second_session(runtime: AsyncioRunner) -> None:
  analyzer = RecordingAnalyzer()
  settings = AppConfig(testing=True)

  @dataclass
  class StickySession(FakePeerSession):

    async def close(self) -> None:
      self.closed = True

  manager = SessionManager(
      runtime=runtime,
      analyzer=analyzer,
      settings=settings,
      session_factory=lambda context, callbacks: StickySession(context=context,
                                                              callbacks=callbacks),
  )

  manager.accept_offer(SessionDescriptionDto(sdp="offer-one", type="offer"))

  with pytest.raises(SessionBusyError):
    manager.accept_offer(SessionDescriptionDto(sdp="offer-two", type="offer"))


def test_session_manager_close_resets_state(manager) -> None:
  session_manager, analyzer = manager
  session_manager.accept_offer(SessionDescriptionDto(sdp="offer-sdp", type="offer"))

  session_manager.close_active_session()
  status = session_manager.get_status()

  assert status.state == "idle"
  assert status.active is False
  assert analyzer.snapshot().sessions_stopped == 1


def test_asyncio_runner_uses_background_thread(runtime: AsyncioRunner) -> None:
  main_thread_name = threading.current_thread().name

  async def identify_thread() -> str:
    await asyncio.sleep(0)
    return threading.current_thread().name

  worker_thread_name = runtime.run(identify_thread())

  assert worker_thread_name != main_thread_name
  assert worker_thread_name == runtime.thread_name
