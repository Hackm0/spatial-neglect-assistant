from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Protocol
from uuid import uuid4

from mobile_ingestion.analyzer import AnalyzerPort
from mobile_ingestion.config import AppConfig
from mobile_ingestion.dto import (RoomStatusDto, SessionDescriptionDto,
                                  SessionOfferRequestDto,
                                  SessionOfferResponseDto,
                                  SessionSlotStatusDto)
from mobile_ingestion.mode_manager import RuntimeModePort
from mobile_ingestion.object_search import ObjectSearchPort
from mobile_ingestion.runtime import AsyncioRunner
from mobile_ingestion.voice import VoiceProcessingPort

SESSION_TOKEN_HEADER = "X-Session-Token"


class SessionBusyError(RuntimeError):
  pass


class SessionUnavailableError(RuntimeError):
  pass


class InvalidSessionError(RuntimeError):
  pass


class SessionAuthorizationError(RuntimeError):
  pass


class SessionPermissionError(RuntimeError):
  pass


@dataclass(frozen=True, slots=True)
class SessionContext:
  session_id: str
  role: str
  started_at: datetime
  analyzer: AnalyzerPort
  voice_processor: VoiceProcessingPort
  object_search: ObjectSearchPort
  runtime_mode: RuntimeModePort
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


@dataclass(slots=True)
class ManagedSession:
  role: str
  token: str
  session: PeerSessionPort
  session_id: str
  started_at: datetime
  state: str = "connecting"
  connection_state: str = "connecting"
  has_video_track: bool = False
  has_audio_track: bool = False
  error: str | None = None


class SessionManager:

  def __init__(self, *, runtime: AsyncioRunner, analyzer: AnalyzerPort,
               voice_processor: VoiceProcessingPort,
               object_search: ObjectSearchPort,
               runtime_mode: RuntimeModePort, settings: AppConfig,
               session_factory: PeerSessionFactory) -> None:
    self._runtime = runtime
    self._analyzer = analyzer
    self._voice_processor = voice_processor
    self._object_search = object_search
    self._runtime_mode = runtime_mode
    self._settings = settings
    self._session_factory = session_factory
    self._lock = Lock()
    self._sessions: dict[str, ManagedSession | None] = {
        "sender": None,
        "spectator": None,
    }

  def accept_offer(self,
                   offer: SessionOfferRequestDto) -> SessionOfferResponseDto:
    if offer.type != "offer":
      raise InvalidSessionError("Only WebRTC offers can open a session.")

    managed_session = self._create_session(offer.role)
    try:
      answer = self._runtime.run(
          managed_session.session.accept_offer(offer.to_description()))
    except Exception as exc:
      self._record_error(offer.role, str(exc))
      self._safe_close(managed_session.session)
      if offer.role == "spectator" and not self._is_sender_video_available():
        raise SessionUnavailableError(
            "A sender with live video is required before a spectator can connect.") from exc
      raise
    return SessionOfferResponseDto.from_description(
        answer,
        role=managed_session.role,
        session_token=managed_session.token,
    )

  def get_status(self) -> RoomStatusDto:
    with self._lock:
      sender = self._sessions["sender"]
      spectator = self._sessions["spectator"]
      sender_dto = self._slot_status_from_managed_session(sender)
      spectator_dto = self._slot_status_from_managed_session(spectator)
      return RoomStatusDto(
          room_state=self._room_state(sender, spectator),
          sender_occupied=sender is not None,
          spectator_occupied=spectator is not None,
          sender_video_available=bool(sender is not None and sender.has_video_track),
          sender=sender_dto,
          spectator=spectator_dto,
          analyzer_metrics=self._analyzer.snapshot(),
      )

  def close_session(self, session_token: str | None) -> None:
    if not session_token:
      return

    with self._lock:
      session = self._session_for_token_locked(session_token)
    if session is None:
      return
    self._safe_close(session.session)

  def assert_sender_session(self, session_token: str | None) -> None:
    with self._lock:
      if not session_token:
        raise SessionAuthorizationError("Missing session token.")
      sender = self._sessions["sender"]
      spectator = self._sessions["spectator"]
      if sender is not None and sender.token == session_token:
        return
      if spectator is not None and spectator.token == session_token:
        raise SessionPermissionError("Spectator sessions are read-only.")
    raise SessionAuthorizationError("Invalid or expired session token.")

  def shutdown(self) -> None:
    with self._lock:
      active_sessions = tuple(
          session for session in self._sessions.values() if session is not None)
    for managed_session in active_sessions:
      self._safe_close(managed_session.session)
    self._runtime.stop(timeout=self._settings.session_shutdown_timeout_seconds)

  def _create_session(self, role: str) -> ManagedSession:
    with self._lock:
      if self._sessions[role] is not None:
        raise SessionBusyError(f"A {role} session is already active.")
      if role == "spectator" and not self._sender_is_ready_for_spectator_locked():
        raise SessionUnavailableError(
            "A sender with live video is required before a spectator can connect.")

      context = SessionContext(
          session_id=str(uuid4()),
          role=role,
          started_at=datetime.now(timezone.utc),
          analyzer=self._analyzer,
          voice_processor=self._voice_processor,
          object_search=self._object_search,
            runtime_mode=self._runtime_mode,
          settings=self._settings,
      )
      callbacks = self._build_callbacks(role)
      session = self._session_factory(context, callbacks)
      managed_session = ManagedSession(
          role=role,
          token=str(uuid4()),
          session=session,
          session_id=context.session_id,
          started_at=context.started_at,
      )
      self._sessions[role] = managed_session

    if role == "sender":
      try:
        self._voice_processor.start_session(context.session_id)
        self._object_search.start_session(context.session_id)
        self._runtime_mode.start_session(context.session_id)
      except Exception:
        self._clear_managed_session(role, expected_token=managed_session.token)
        self._runtime_mode.stop_session(context.session_id)
        self._object_search.stop_session(context.session_id)
        self._voice_processor.stop_session(context.session_id)
        self._safe_close(managed_session.session)
        raise

    return managed_session

  def _build_callbacks(self, role: str) -> SessionCallbacks:
    return SessionCallbacks(
        on_connection_state_changed=lambda connection_state:
        self._update_connection_state(role, connection_state),
        on_video_track_detected=lambda: self._mark_video_track_detected(role),
        on_audio_track_detected=lambda: self._mark_audio_track_detected(role),
        on_error=lambda message: self._record_error(role, message),
        on_closed=lambda: self._clear_closed_session(role),
    )

  def _safe_close(self, session: PeerSessionPort) -> None:
    if threading.current_thread().name == self._runtime.thread_name:
      asyncio.create_task(session.close())
      return
    try:
      self._runtime.run(
          session.close(),
          timeout=self._settings.session_shutdown_timeout_seconds,
      )
    except Exception:
      # Close paths are best-effort; the callback cleanup still guards state.
      return

  def _update_connection_state(self, role: str, connection_state: str) -> None:
    with self._lock:
      session = self._sessions.get(role)
      if session is None:
        return
      session.connection_state = connection_state
      if connection_state == "connected":
        session.state = "streaming"
        session.error = None
      elif connection_state in {"failed", "disconnected"}:
        session.state = "error"
        session.error = f"Peer connection entered '{connection_state}'."
      elif connection_state in {"new", "connecting"}:
        session.state = "connecting"

  def _mark_video_track_detected(self, role: str) -> None:
    with self._lock:
      session = self._sessions.get(role)
      if session is not None:
        session.has_video_track = True

  def _mark_audio_track_detected(self, role: str) -> None:
    with self._lock:
      session = self._sessions.get(role)
      if session is not None:
        session.has_audio_track = True

  def _record_error(self, role: str, message: str) -> None:
    with self._lock:
      session = self._sessions.get(role)
      if session is None:
        return
      session.state = "error"
      session.error = message

  def _clear_closed_session(self, role: str) -> None:
    stopped_sender_session_id: str | None = None
    spectator_to_close: PeerSessionPort | None = None
    with self._lock:
      session = self._sessions.get(role)
      if session is None:
        return
      if role == "sender":
        stopped_sender_session_id = session.session_id
        spectator = self._sessions.get("spectator")
        if spectator is not None:
          spectator_to_close = spectator.session
      self._sessions[role] = None

    if stopped_sender_session_id is not None:
      self._object_search.stop_session(stopped_sender_session_id)
      self._runtime_mode.stop_session(stopped_sender_session_id)
      self._voice_processor.stop_session(stopped_sender_session_id)
    if spectator_to_close is not None:
      self._safe_close(spectator_to_close)

  def _clear_managed_session(self, role: str, *, expected_token: str) -> None:
    with self._lock:
      session = self._sessions.get(role)
      if session is not None and session.token == expected_token:
        self._sessions[role] = None

  def _session_for_token_locked(self,
                                session_token: str) -> ManagedSession | None:
    for session in self._sessions.values():
      if session is not None and session.token == session_token:
        return session
    return None

  def _sender_is_ready_for_spectator_locked(self) -> bool:
    sender = self._sessions["sender"]
    return sender is not None and sender.has_video_track

  def _is_sender_video_available(self) -> bool:
    with self._lock:
      return self._sender_is_ready_for_spectator_locked()

  @staticmethod
  def _room_state(sender: ManagedSession | None,
                  spectator: ManagedSession | None) -> str:
    if sender is None:
      return "idle"
    if sender.has_video_track and spectator is not None:
      return "full"
    if sender.has_video_track:
      return "sender_streaming"
    return "sender_connecting"

  @staticmethod
  def _slot_status_from_managed_session(
      session: ManagedSession | None) -> SessionSlotStatusDto | None:
    if session is None:
      return None
    return SessionSlotStatusDto.from_values(
        role=session.role,
        state=session.state,
        active=True,
        session_id=session.session_id,
        connection_state=session.connection_state,
        has_video_track=session.has_video_track,
        has_audio_track=session.has_audio_track,
        started_at=session.started_at,
        error=session.error,
    )
