from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

from mobile_ingestion.arduino import ArduinoControllerPort
from uart_protocol import ActuatorCommand


class ObjectFeedbackPort(Protocol):

  def start_session(self, session_id: str) -> None:
    raise NotImplementedError

  def stop_session(self, session_id: str) -> None:
    raise NotImplementedError

  def notify_target_detected(self, session_id: str) -> None:
    raise NotImplementedError

  def clear(self, session_id: str) -> None:
    raise NotImplementedError

  def shutdown(self) -> None:
    raise NotImplementedError


class NoOpObjectFeedback(ObjectFeedbackPort):

  def start_session(self, session_id: str) -> None:
    del session_id

  def stop_session(self, session_id: str) -> None:
    del session_id

  def notify_target_detected(self, session_id: str) -> None:
    del session_id

  def clear(self, session_id: str) -> None:
    del session_id

  def shutdown(self) -> None:
    return None


@dataclass(frozen=True, slots=True)
class _FeedbackState:
  active_session_id: str | None
  burst_started_at: float | None
  burst_deadline: float | None
  owns_vibration: bool


class ArduinoBurstFeedbackController(ObjectFeedbackPort):

  def __init__(
      self,
      *,
      controller: ArduinoControllerPort,
      burst_on_seconds: float = 0.25,
      burst_period_seconds: float = 1.0,
      burst_duration_seconds: float = 10.0,
      tick_seconds: float = 0.02,
  ) -> None:
    self._controller = controller
    self._burst_on_seconds = max(0.01, burst_on_seconds)
    self._burst_period_seconds = max(self._burst_on_seconds,
                                     burst_period_seconds)
    self._burst_duration_seconds = max(self._burst_period_seconds,
                                       burst_duration_seconds)
    self._tick_seconds = max(0.01, tick_seconds)
    self._lock = threading.Lock()
    self._state = _FeedbackState(
        active_session_id=None,
        burst_started_at=None,
        burst_deadline=None,
        owns_vibration=False,
    )
    self._wake_event = threading.Event()
    self._stop_event = threading.Event()
    self._thread = threading.Thread(
        target=self._run,
        name="object-feedback-burst",
        daemon=True,
    )
    self._thread.start()

  def start_session(self, session_id: str) -> None:
    with self._lock:
      self._state = _FeedbackState(
          active_session_id=session_id,
          burst_started_at=None,
          burst_deadline=None,
          owns_vibration=self._state.owns_vibration,
      )
    self._wake_event.set()

  def stop_session(self, session_id: str) -> None:
    with self._lock:
      if session_id != self._state.active_session_id:
        return
      self._state = _FeedbackState(
          active_session_id=None,
          burst_started_at=None,
          burst_deadline=None,
          owns_vibration=self._state.owns_vibration,
      )
    self._wake_event.set()

  def notify_target_detected(self, session_id: str) -> None:
    now = time.monotonic()
    with self._lock:
      if session_id != self._state.active_session_id:
        return
      self._state = _FeedbackState(
          active_session_id=self._state.active_session_id,
          burst_started_at=now,
          burst_deadline=now + self._burst_duration_seconds,
          owns_vibration=True,
      )
    self._wake_event.set()

  def clear(self, session_id: str) -> None:
    with self._lock:
      if session_id != self._state.active_session_id:
        return
      self._state = _FeedbackState(
          active_session_id=self._state.active_session_id,
          burst_started_at=None,
          burst_deadline=None,
          owns_vibration=self._state.owns_vibration,
      )
    self._wake_event.set()

  def shutdown(self) -> None:
    self._stop_event.set()
    self._wake_event.set()
    self._thread.join(timeout=1.0)
    self._ensure_vibration_disabled()

  def _run(self) -> None:
    while not self._stop_event.is_set():
      self._tick()
      self._wake_event.wait(self._tick_seconds)
      self._wake_event.clear()
    self._ensure_vibration_disabled()

  def _tick(self) -> None:
    now = time.monotonic()
    with self._lock:
      state = self._state
      burst_active = (state.burst_started_at is not None
                      and state.burst_deadline is not None
                      and now < state.burst_deadline)
      desired_vibration = False
      if burst_active:
        elapsed = now - state.burst_started_at
        desired_vibration = ((elapsed % self._burst_period_seconds)
                             < self._burst_on_seconds)
      owns_vibration = state.owns_vibration

    if not burst_active and not owns_vibration:
      return

    snapshot = self._controller.get_snapshot()
    if snapshot.backend_command.vibration_enabled != desired_vibration:
      self._controller.set_backend_command(
          ActuatorCommand(
              servo_angle_degrees=snapshot.backend_command.servo_angle_degrees,
              vibration_enabled=desired_vibration,
          ))

    if not burst_active:
      with self._lock:
        if self._state.burst_started_at is None and self._state.owns_vibration:
          self._state = _FeedbackState(
              active_session_id=self._state.active_session_id,
              burst_started_at=None,
              burst_deadline=None,
              owns_vibration=False,
          )

  def _ensure_vibration_disabled(self) -> None:
    snapshot = self._controller.get_snapshot()
    if not snapshot.backend_command.vibration_enabled:
      return
    self._controller.set_backend_command(
        ActuatorCommand(
            servo_angle_degrees=snapshot.backend_command.servo_angle_degrees,
            vibration_enabled=False,
        ))
