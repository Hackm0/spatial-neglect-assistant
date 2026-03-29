from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol

from mobile_ingestion.arduino import ArduinoControllerPort
from mobile_ingestion.eating_detection import (EatingDetectionResult,
                                               EatingDetectorPort)
from mobile_ingestion.object_search import (ObjectSearchEvent, ObjectSearchFrame,
                                            ObjectSearchPort, ObjectSearchStatus,
                                            ObjectSearchSubscription)
from mobile_ingestion.voice import (VoiceEvent, VoiceProcessingPort,
                                    VoiceSubscription, WakeWordEvent)
from uart_protocol import ActuatorCommand


def _utcnow() -> datetime:
  return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RuntimeModeStatus:
  available: bool
  active: bool
  session_id: str | None
  mode: str
  detail: str | None
  error: str | None
  plate_visible: bool | None = None
  is_eating: bool | None = None
  one_side_food_remaining: bool | None = None
  remaining_side: str | None = None
  last_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuntimeModeEvent:
  event_type: str
  payload: RuntimeModeStatus


@dataclass(frozen=True, slots=True)
class RuntimeModeSubscription:
  subscription_id: int
  events: "queue.Queue[RuntimeModeEvent | None]"


class RuntimeModePort(Protocol):

  def start_session(self, session_id: str) -> None:
    raise NotImplementedError

  def stop_session(self, session_id: str) -> None:
    raise NotImplementedError

  def submit_frame(self, frame: ObjectSearchFrame) -> None:
    raise NotImplementedError

  def snapshot(self) -> RuntimeModeStatus:
    raise NotImplementedError

  def subscribe(self) -> RuntimeModeSubscription:
    raise NotImplementedError

  def unsubscribe(self, subscription: RuntimeModeSubscription) -> None:
    raise NotImplementedError

  def shutdown(self) -> None:
    raise NotImplementedError


class RuntimeModeManager(RuntimeModePort):

  def __init__(
      self,
      *,
      voice_processor: VoiceProcessingPort,
      object_search: ObjectSearchPort,
      arduino_controller: ArduinoControllerPort,
      eating_detector: EatingDetectorPort,
      idle_check_interval_seconds: float,
      eating_check_interval_seconds: float,
      eating_streak_required: int,
      eating_vibration_seconds: float,
      eating_vibration_cooldown_seconds: float,
      object_search_completion_seconds: float,
  ) -> None:
    self._voice_processor = voice_processor
    self._object_search = object_search
    self._arduino_controller = arduino_controller
    self._eating_detector = eating_detector
    self._idle_check_interval_seconds = max(1.0, idle_check_interval_seconds)
    self._eating_check_interval_seconds = max(1.0, eating_check_interval_seconds)
    self._eating_streak_required = max(1, eating_streak_required)
    self._eating_vibration_seconds = max(0.1, eating_vibration_seconds)
    self._eating_vibration_cooldown_seconds = max(
        self._eating_vibration_seconds,
        eating_vibration_cooldown_seconds,
    )
    self._object_search_completion_seconds = max(0.1,
                                                 object_search_completion_seconds)

    self._lock = threading.Lock()
    self._next_subscription_id = 1
    self._subscriptions: dict[int, "queue.Queue[RuntimeModeEvent | None]"] = {}
    self._active_session_id: str | None = None
    self._status = RuntimeModeStatus(
        available=True,
        active=False,
        session_id=None,
        mode="idle",
        detail="Mode idle.",
        error=None,
        last_updated_at=_utcnow(),
    )

    self._voice_subscription: VoiceSubscription | None = None
    self._object_subscription: ObjectSearchSubscription | None = None
    self._voice_thread: threading.Thread | None = None
    self._object_thread: threading.Thread | None = None
    self._mode_thread: threading.Thread | None = None
    self._stop_event: threading.Event | None = None

    self._latest_frame: ObjectSearchFrame | None = None
    self._next_idle_check_at = 0.0
    self._next_eating_check_at = 0.0
    self._idle_plate_streak = 0
    self._eating_streak = 0
    self._eating_vibration_until: float | None = None
    self._eating_notice_until: float | None = None
    self._next_eating_vibration_allowed_at = 0.0
    self._object_search_completion_deadline: float | None = None

  def start_session(self, session_id: str) -> None:
    previous_session_id = None
    with self._lock:
      previous_session_id = self._active_session_id
    if previous_session_id is not None:
      self.stop_session(previous_session_id)

    stop_event = threading.Event()
    voice_subscription = self._voice_processor.subscribe()
    object_subscription = self._object_search.subscribe()
    voice_thread = threading.Thread(
        target=self._run_voice_worker,
        args=(session_id, voice_subscription, stop_event),
        name=f"mode-voice-{session_id[:8]}",
        daemon=True,
    )
    object_thread = threading.Thread(
        target=self._run_object_worker,
        args=(session_id, object_subscription, stop_event),
        name=f"mode-object-{session_id[:8]}",
        daemon=True,
    )
    mode_thread = threading.Thread(
        target=self._run_mode_worker,
        args=(session_id, stop_event),
        name=f"mode-loop-{session_id[:8]}",
        daemon=True,
    )

    now = time.monotonic()
    with self._lock:
      self._active_session_id = session_id
      self._voice_subscription = voice_subscription
      self._object_subscription = object_subscription
      self._voice_thread = voice_thread
      self._object_thread = object_thread
      self._mode_thread = mode_thread
      self._stop_event = stop_event
      self._latest_frame = None
      self._next_idle_check_at = now + self._idle_check_interval_seconds
      self._next_eating_check_at = now + self._eating_check_interval_seconds
      self._idle_plate_streak = 0
      self._eating_streak = 0
      self._eating_vibration_until = None
      self._eating_notice_until = None
      self._next_eating_vibration_allowed_at = now
      self._object_search_completion_deadline = None
      self._status = RuntimeModeStatus(
          available=True,
          active=True,
          session_id=session_id,
          mode="idle",
          detail="Mode idle.",
          error=(self._eating_detector.error if not self._eating_detector.available
                 else None),
          last_updated_at=_utcnow(),
      )

    voice_thread.start()
    object_thread.start()
    mode_thread.start()
    self._broadcast_status()

  def stop_session(self, session_id: str) -> None:
    voice_subscription = None
    object_subscription = None
    voice_thread = None
    object_thread = None
    mode_thread = None
    stop_event = None

    with self._lock:
      if session_id != self._active_session_id:
        return
      voice_subscription = self._voice_subscription
      object_subscription = self._object_subscription
      voice_thread = self._voice_thread
      object_thread = self._object_thread
      mode_thread = self._mode_thread
      stop_event = self._stop_event
      self._active_session_id = None
      self._voice_subscription = None
      self._object_subscription = None
      self._voice_thread = None
      self._object_thread = None
      self._mode_thread = None
      self._stop_event = None
      self._latest_frame = None
      self._idle_plate_streak = 0
      self._eating_streak = 0
      self._eating_vibration_until = None
      self._eating_notice_until = None
      self._object_search_completion_deadline = None
      self._status = RuntimeModeStatus(
          available=True,
          active=False,
          session_id=None,
          mode="idle",
          detail="Mode idle.",
          error=None,
          last_updated_at=_utcnow(),
      )

    if stop_event is not None:
      stop_event.set()
    if voice_thread is not None:
      voice_thread.join(timeout=2.0)
    if object_thread is not None:
      object_thread.join(timeout=2.0)
    if mode_thread is not None:
      mode_thread.join(timeout=2.0)
    if voice_subscription is not None:
      self._voice_processor.unsubscribe(voice_subscription)
    if object_subscription is not None:
      self._object_search.unsubscribe(object_subscription)

    self._set_backend_vibration(False)
    self._broadcast_status()

  def submit_frame(self, frame: ObjectSearchFrame) -> None:
    with self._lock:
      if frame.session_id != self._active_session_id:
        return
      self._latest_frame = frame

  def snapshot(self) -> RuntimeModeStatus:
    with self._lock:
      return self._status

  def subscribe(self) -> RuntimeModeSubscription:
    event_queue: "queue.Queue[RuntimeModeEvent | None]" = queue.Queue()
    with self._lock:
      subscription_id = self._next_subscription_id
      self._next_subscription_id += 1
      self._subscriptions[subscription_id] = event_queue
      snapshot = self._status
    event_queue.put(RuntimeModeEvent("status", snapshot))
    return RuntimeModeSubscription(subscription_id=subscription_id,
                                   events=event_queue)

  def unsubscribe(self, subscription: RuntimeModeSubscription) -> None:
    with self._lock:
      self._subscriptions.pop(subscription.subscription_id, None)

  def shutdown(self) -> None:
    active_session_id = None
    with self._lock:
      active_session_id = self._active_session_id
    if active_session_id is not None:
      self.stop_session(active_session_id)

    with self._lock:
      subscriptions = tuple(self._subscriptions.values())
      self._subscriptions.clear()
    for subscription_queue in subscriptions:
      subscription_queue.put(None)

  def _run_voice_worker(
      self,
      session_id: str,
      voice_subscription: VoiceSubscription,
      stop_event: threading.Event,
  ) -> None:
    while not stop_event.is_set():
      try:
        event = voice_subscription.events.get(timeout=0.25)
      except queue.Empty:
        continue
      if event is None:
        break
      self._handle_voice_event(session_id, event)

  def _run_object_worker(
      self,
      session_id: str,
      object_subscription: ObjectSearchSubscription,
      stop_event: threading.Event,
  ) -> None:
    while not stop_event.is_set():
      try:
        event = object_subscription.events.get(timeout=0.25)
      except queue.Empty:
        continue
      if event is None:
        break
      self._handle_object_event(session_id, event)

  def _run_mode_worker(self, session_id: str, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
      now = time.monotonic()
      self._apply_mode_timers(session_id, now)
      time.sleep(0.25)

  def _handle_voice_event(self, session_id: str, event: VoiceEvent) -> None:
    if not isinstance(event.payload, WakeWordEvent):
      return
    wake_event = event.payload
    if wake_event.session_id != session_id:
      return
    self._set_mode(session_id, mode="object_search", detail="Recherche d'objet activee par Jarvis.")

  def _handle_object_event(self, session_id: str, event: ObjectSearchEvent) -> None:
    payload = event.payload
    if not isinstance(payload, ObjectSearchStatus):
      return
    if payload.session_id != session_id:
      return

    if payload.state in {"awaiting_request", "resolving_target", "searching", "found"}:
      self._set_mode(session_id, mode="object_search", detail="Recherche d'objet en cours.")

    if payload.state == "idle":
      with self._lock:
        deadline = self._object_search_completion_deadline
      if deadline is None:
        self._set_mode(session_id, mode="idle", detail="Mode idle.")

    if payload.state == "found" and payload.detected:
      with self._lock:
        if self._active_session_id != session_id:
          return
        if self._object_search_completion_deadline is None:
          self._object_search_completion_deadline = (
              time.monotonic() + self._object_search_completion_seconds)

  def _apply_mode_timers(self, session_id: str, now: float) -> None:
    mode = None
    with self._lock:
      if self._active_session_id != session_id:
        return
      mode = self._status.mode

    if mode == "object_search":
      self._apply_object_search_timeout(session_id, now)
      self._apply_eating_vibration_timeout(session_id, now)
      self._apply_eating_notice_timeout(session_id, now)
      return

    if mode == "idle":
      self._apply_idle_check(session_id, now)
    elif mode == "eating":
      self._apply_eating_check(session_id, now)

    self._apply_eating_vibration_timeout(session_id, now)
    self._apply_eating_notice_timeout(session_id, now)

  def _apply_idle_check(self, session_id: str, now: float) -> None:
    frame = None
    should_run = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      if now >= self._next_idle_check_at:
        should_run = True
        self._next_idle_check_at = now + self._idle_check_interval_seconds
        frame = self._latest_frame
    if not should_run or frame is None:
      return

    result = self._run_eating_detection(session_id, frame)
    if result is None:
      return

    enter_eating = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      if result.plate_visible:
        self._idle_plate_streak += 1
      else:
        self._idle_plate_streak = 0
      # Allow entry when eating is explicit OR plate is stably visible OR
      # side imbalance is already clear; this reduces missed transitions.
      enter_eating = (
          result.plate_visible
          and (
              result.is_eating
              or result.one_side_food_remaining
              or self._idle_plate_streak >= 2
          ))

    if enter_eating:
      self._set_mode(
          session_id,
          mode="eating",
          detail="Mode repas active.",
          detection_result=result,
      )

  def _apply_eating_check(self, session_id: str, now: float) -> None:
    frame = None
    should_run = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      if now >= self._next_eating_check_at:
        should_run = True
        self._next_eating_check_at = now + self._eating_check_interval_seconds
        frame = self._latest_frame
    if not should_run or frame is None:
      return

    result = self._run_eating_detection(session_id, frame)
    if result is None:
      return

    if not result.plate_visible:
      with self._lock:
        self._eating_streak = 0
      self._set_mode(
          session_id,
          mode="idle",
          detail="Assiette non detectee, retour en mode idle.",
          detection_result=result,
      )
      self._set_backend_vibration(False)
      return

    should_vibrate = False
    detail = "Mode repas actif."
    with self._lock:
      if self._active_session_id != session_id:
        return
      if result.one_side_food_remaining:
        self._eating_streak += 1
      else:
        self._eating_streak = 0

      if (self._eating_streak >= self._eating_streak_required
          and now >= self._next_eating_vibration_allowed_at):
        self._eating_vibration_until = now + self._eating_vibration_seconds
        self._eating_notice_until = now + min(3.0, self._eating_vibration_seconds)
        self._next_eating_vibration_allowed_at = (
            now + self._eating_vibration_cooldown_seconds)
        should_vibrate = True

      if should_vibrate:
        detail = self._side_notice_text(result.remaining_side)
      elif self._eating_notice_until is not None and now < self._eating_notice_until:
        detail = self._status.detail or "Mode repas actif."

    self._set_mode(
        session_id,
        mode="eating",
        detail=detail,
        detection_result=result,
    )

    if should_vibrate:
      self._set_backend_vibration(True)

  def _apply_object_search_timeout(self, session_id: str, now: float) -> None:
    should_complete = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      deadline = self._object_search_completion_deadline
      if deadline is not None and now >= deadline:
        should_complete = True
        self._object_search_completion_deadline = None

    if not should_complete:
      return

    self._object_search.cancel_active_search(session_id)
    self._set_mode(
        session_id,
        mode="idle",
        detail="Recherche terminee, retour en mode idle.",
    )

  def _apply_eating_vibration_timeout(self, session_id: str, now: float) -> None:
    should_disable = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      deadline = self._eating_vibration_until
      if deadline is not None and now >= deadline:
        self._eating_vibration_until = None
        should_disable = True

    if should_disable:
      self._set_backend_vibration(False)

  def _apply_eating_notice_timeout(self, session_id: str, now: float) -> None:
    should_clear_notice = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      if self._status.mode != "eating":
        self._eating_notice_until = None
        return
      if self._eating_notice_until is not None and now >= self._eating_notice_until:
        self._eating_notice_until = None
        should_clear_notice = True

    if should_clear_notice:
      self._set_mode(
          session_id,
          mode="eating",
          detail="Mode repas actif.",
      )

  def _run_eating_detection(
      self,
      session_id: str,
      frame: ObjectSearchFrame,
  ) -> EatingDetectionResult | None:
    try:
      result = self._eating_detector.detect(frame)
    except Exception as exc:
      self._set_mode(
          session_id,
          mode=self.snapshot().mode,
          detail="Erreur de detection repas.",
          error=str(exc),
      )
      return None

    self._set_mode(
        session_id,
        mode=self.snapshot().mode,
        detail=self.snapshot().detail,
        detection_result=result,
        error=(self._eating_detector.error if not self._eating_detector.available
               else None),
    )
    return result

  def _set_mode(
      self,
      session_id: str,
      *,
      mode: str,
      detail: str | None,
      detection_result: EatingDetectionResult | None = None,
      error: str | None = None,
  ) -> None:
    changed = False
    with self._lock:
      if self._active_session_id != session_id:
        return
      if mode == "object_search":
        self._idle_plate_streak = 0
        self._eating_streak = 0
        self._eating_vibration_until = None
        self._eating_notice_until = None

      updated = replace(
          self._status,
          available=True,
          active=True,
          session_id=session_id,
          mode=mode,
          detail=detail,
          error=error,
          plate_visible=(detection_result.plate_visible
                        if detection_result is not None else self._status.plate_visible),
          is_eating=(detection_result.is_eating
                     if detection_result is not None else self._status.is_eating),
          one_side_food_remaining=(
              detection_result.one_side_food_remaining
              if detection_result is not None else self._status.one_side_food_remaining),
          remaining_side=(detection_result.remaining_side
                          if detection_result is not None else self._status.remaining_side),
          last_updated_at=_utcnow(),
      )
      if updated != self._status:
        self._status = updated
        changed = True

    if changed:
      self._broadcast_status()

  def _set_backend_vibration(self, enabled: bool) -> None:
    snapshot = self._arduino_controller.get_snapshot()
    if snapshot.backend_command.vibration_enabled == enabled:
      return
    self._arduino_controller.set_backend_command(
        ActuatorCommand(
            servo_angle_degrees=snapshot.backend_command.servo_angle_degrees,
            vibration_enabled=enabled,
        ))

  def _broadcast_status(self) -> None:
    with self._lock:
      subscribers = tuple(self._subscriptions.values())
      payload = self._status
    for subscriber in subscribers:
      subscriber.put(RuntimeModeEvent("status", payload))

  def _side_notice_text(self, remaining_side: str | None) -> str:
    if remaining_side == "left":
      return "Repas detecte: cote droit vide, cote gauche encore plein."
    if remaining_side == "right":
      return "Repas detecte: cote gauche vide, cote droit encore plein."
    return "Repas detecte: nourriture restante surtout d'un seul cote."
