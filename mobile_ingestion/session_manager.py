from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Protocol
from uuid import uuid4

from mobile_ingestion.analyzer import AnalyzerPort
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import SessionDescriptionDto, SessionStatusDto
from mobile_ingestion.object_search import ObjectSearchPort
from mobile_ingestion.runtime import AsyncioRunner
from mobile_ingestion.voice import VoiceProcessingPort


class SessionBusyError(RuntimeError):
  pass


class InvalidSessionError(RuntimeError):
  pass


@dataclass(frozen=True, slots=True)
class SessionContext:
  session_id: str
  started_at: datetime
  analyzer: AnalyzerPort
  voice_processor: VoiceProcessingPort
  object_search: ObjectSearchPort
  settings: AppConfig


@dataclass(frozen=True, slots=True)
class SessionCallbacks:
  on_connection_state_changed: Callable[[str], None]
  on_video_track_detected: Callable[[], None]
  on_audio_track_detected: Callable[[], None]
  on_error: Callable[[str], None]
  on_closed: Callable[[], None]


class PeerSessionPort(Protocol):

  async def accept_offer(
      self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    raise NotImplementedError

  async def close(self) -> None:
    raise NotImplementedError


PeerSessionFactory = Callable[[SessionContext, SessionCallbacks], PeerSessionPort]


class SessionManager:

  def __init__(self, *, runtime: AsyncioRunner, analyzer: AnalyzerPort,
               voice_processor: VoiceProcessingPort,
               object_search: ObjectSearchPort, settings: AppConfig,
               session_factory: PeerSessionFactory) -> None:
    self._runtime = runtime
    self._analyzer = analyzer
    self._voice_processor = voice_processor
    self._object_search = object_search
    self._settings = settings
    self._session_factory = session_factory
    self._lock = Lock()
    self._session: PeerSessionPort | None = None
    self._state = "idle"
    self._session_id: str | None = None
    self._connection_state = "new"
    self._has_video_track = False
    self._has_audio_track = False
    self._started_at: datetime | None = None
    self._error: str | None = None

  def accept_offer(self, offer: SessionDescriptionDto) -> SessionDescriptionDto:
    if offer.type != "offer":
      raise InvalidSessionError("Only WebRTC offers can open a session.")

    session = self._create_session()
    try:
      answer = self._runtime.run(session.accept_offer(offer))
    except Exception as exc:
      self._record_error(str(exc))
      self._safe_close(session)
      raise
    return answer

  def get_status(self) -> SessionStatusDto:
    with self._lock:
      return SessionStatusDto.from_values(
          state=self._state,
          active=self._session is not None,
          session_id=self._session_id,
          connection_state=self._connection_state,
          has_video_track=self._has_video_track,
          has_audio_track=self._has_audio_track,
          started_at=self._started_at,
          error=self._error,
          analyzer_metrics=self._analyzer.snapshot(),
      )

  def close_active_session(self) -> None:
    with self._lock:
      session = self._session
    if session is None:
      self._set_idle_state()
      return
    self._safe_close(session)

  def shutdown(self) -> None:
    self.close_active_session()
    self._runtime.stop(timeout=self._settings.session_shutdown_timeout_seconds)

  def _create_session(self) -> PeerSessionPort:
    with self._lock:
      if self._session is not None:
        raise SessionBusyError("A mobile session is already active.")

      context = SessionContext(
          session_id=str(uuid4()),
          started_at=datetime.now(timezone.utc),
          analyzer=self._analyzer,
          voice_processor=self._voice_processor,
          object_search=self._object_search,
          settings=self._settings,
      )
      callbacks = SessionCallbacks(
          on_connection_state_changed=self._update_connection_state,
          on_video_track_detected=self._mark_video_track_detected,
          on_audio_track_detected=self._mark_audio_track_detected,
          on_error=self._record_error,
          on_closed=self._clear_closed_session,
      )
      session = self._session_factory(context, callbacks)
      self._session = session
      self._state = "connecting"
      self._session_id = context.session_id
      self._connection_state = "connecting"
      self._has_video_track = False
      self._has_audio_track = False
      self._started_at = context.started_at
      self._error = None
    try:
      self._voice_processor.start_session(context.session_id)
      self._object_search.start_session(context.session_id)
    except Exception:
      self._object_search.stop_session(context.session_id)
      self._voice_processor.stop_session(context.session_id)
      self._set_idle_state()
      raise
    return session

  def _safe_close(self, session: PeerSessionPort) -> None:
    try:
      self._runtime.run(
          session.close(),
          timeout=self._settings.session_shutdown_timeout_seconds,
      )
    finally:
      self._clear_closed_session()

  def _update_connection_state(self, connection_state: str) -> None:
    with self._lock:
      self._connection_state = connection_state
      if connection_state == "connected":
        self._state = "streaming"
        self._error = None
      elif connection_state in {"failed", "disconnected"}:
        self._state = "error"
        self._error = f"Peer connection entered '{connection_state}'."
      elif connection_state == "closed" and self._session is not None:
        self._state = "idle"

  def _mark_video_track_detected(self) -> None:
    with self._lock:
      self._has_video_track = True

  def _mark_audio_track_detected(self) -> None:
    with self._lock:
      self._has_audio_track = True

  def _record_error(self, message: str) -> None:
    with self._lock:
      self._state = "error"
      self._error = message

  def _clear_closed_session(self) -> None:
    session_id = None
    with self._lock:
      had_error = self._state == "error"
      session_id = self._session_id
      self._session = None
      self._session_id = None
      self._connection_state = "closed"
      self._has_video_track = False
      self._has_audio_track = False
      self._started_at = None
      if not had_error:
        self._state = "idle"
    if session_id is not None:
      self._object_search.stop_session(session_id)
      self._voice_processor.stop_session(session_id)

  def _set_idle_state(self) -> None:
    session_id = None
    with self._lock:
      session_id = self._session_id
      self._session = None
      self._session_id = None
      self._state = "idle"
      self._connection_state = "closed"
      self._has_video_track = False
      self._has_audio_track = False
      self._started_at = None
      self._error = None
    if session_id is not None:
      self._object_search.stop_session(session_id)
      self._voice_processor.stop_session(session_id)
