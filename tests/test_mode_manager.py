from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from mobile_ingestion.eating_detection import EatingDetectionResult
from mobile_ingestion.mode_manager import RuntimeModeManager
from mobile_ingestion.object_search import ObjectSearchFrame, ObjectSearchSubscription
from mobile_ingestion.voice import VoiceSubscription
from uart_protocol import ActuatorCommand


def wait_until(predicate: object, *, timeout_seconds: float = 3.5) -> None:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.02)
  raise AssertionError("Timed out waiting for runtime mode state.")


class FakeVoiceProcessor:

  def subscribe(self) -> VoiceSubscription:
    return VoiceSubscription(1, queue.Queue())

  def unsubscribe(self, subscription: VoiceSubscription) -> None:
    del subscription


class FakeObjectSearch:

  def __init__(self) -> None:
    self.cancelled_sessions: list[str] = []

  def subscribe(self) -> ObjectSearchSubscription:
    return ObjectSearchSubscription(1, queue.Queue())

  def unsubscribe(self, subscription: ObjectSearchSubscription) -> None:
    del subscription

  def cancel_active_search(self, session_id: str) -> None:
    self.cancelled_sessions.append(session_id)


@dataclass(frozen=True)
class FakeArduinoSnapshot:
  backend_command: ActuatorCommand


class FakeArduinoController:

  def __init__(self) -> None:
    self._snapshot = FakeArduinoSnapshot(
        backend_command=ActuatorCommand(
            servo_angle_degrees=90,
            vibration_enabled=False,
        ))

  def get_snapshot(self) -> FakeArduinoSnapshot:
    return self._snapshot

  def set_backend_command(self, command: ActuatorCommand) -> None:
    self._snapshot = FakeArduinoSnapshot(backend_command=command)


class QueueEatingDetector:

  def __init__(self) -> None:
    self.available = True
    self.error = None
    self.results: "queue.Queue[EatingDetectionResult]" = queue.Queue()

  def detect(self, frame: ObjectSearchFrame) -> EatingDetectionResult:
    del frame
    try:
      return self.results.get_nowait()
    except queue.Empty:
      return EatingDetectionResult(
          plate_visible=False,
          is_eating=False,
          one_side_food_remaining=False,
          remaining_side="none",
          paper_visible=False,
          is_writing=False,
          one_side_writing=False,
          writing_side="none",
      )


def _frame(session_id: str = "session-writing") -> ObjectSearchFrame:
  return ObjectSearchFrame(
      session_id=session_id,
      received_at=datetime.now(timezone.utc),
      image_rgb=object(),
      width=320,
      height=240,
  )


def _result(*, paper_visible: bool, one_side_writing: bool = False,
            writing_side: str = "none") -> EatingDetectionResult:
  return EatingDetectionResult(
      plate_visible=False,
      is_eating=False,
      one_side_food_remaining=False,
      remaining_side="none",
      paper_visible=paper_visible,
      is_writing=paper_visible,
      one_side_writing=one_side_writing,
      writing_side=writing_side,
  )


def _build_manager(*, eating_streak_required: int = 2,
                   vibration_seconds: float = 0.2) -> tuple[RuntimeModeManager, QueueEatingDetector, FakeArduinoController]:
  detector = QueueEatingDetector()
  arduino = FakeArduinoController()
  manager = RuntimeModeManager(
      voice_processor=FakeVoiceProcessor(),
      object_search=FakeObjectSearch(),
      arduino_controller=arduino,
      eating_detector=detector,
      idle_check_interval_seconds=1.0,
      eating_check_interval_seconds=1.0,
      eating_streak_required=eating_streak_required,
      writing_check_interval_seconds=1.0,
      writing_streak_required=1,
      eating_vibration_seconds=vibration_seconds,
      eating_vibration_cooldown_seconds=vibration_seconds,
      object_search_completion_seconds=1.0,
  )
  return manager, detector, arduino


def test_enters_writing_mode_when_paper_is_visible() -> None:
  manager, detector, _ = _build_manager()

  manager.start_session("session-writing")
  manager.submit_frame(_frame())
  detector.results.put(_result(paper_visible=True))

  try:
    wait_until(lambda: manager.snapshot().mode == "writing")
    snapshot = manager.snapshot()
    assert snapshot.mode == "writing"
    assert snapshot.paper_visible is True
    assert snapshot.detail == "Mode ecriture actif."
  finally:
    manager.stop_session("session-writing")


def test_leaves_writing_mode_when_paper_disappears() -> None:
  manager, detector, _ = _build_manager()

  manager.start_session("session-writing")
  manager.submit_frame(_frame())
  detector.results.put(_result(paper_visible=True))

  try:
    wait_until(lambda: manager.snapshot().mode == "writing")
    detector.results.put(_result(paper_visible=False))
    wait_until(lambda: manager.snapshot().mode == "idle", timeout_seconds=4.0)
    snapshot = manager.snapshot()
    assert snapshot.mode == "idle"
    assert snapshot.detail == "Feuille non detectee, retour en mode idle."
  finally:
    manager.stop_session("session-writing")


def test_writing_on_right_side_triggers_vibration_and_notice() -> None:
  manager, detector, arduino = _build_manager(
      eating_streak_required=1,
      vibration_seconds=0.2,
  )

  manager.start_session("session-writing")
  manager.submit_frame(_frame())
  detector.results.put(_result(paper_visible=True))

  try:
    wait_until(lambda: manager.snapshot().mode == "writing")

    detector.results.put(
        _result(
            paper_visible=True,
        one_side_writing=False,
            writing_side="right",
        ))

    wait_until(lambda: arduino.get_snapshot().backend_command.vibration_enabled)
    snapshot = manager.snapshot()
    assert snapshot.mode == "writing"
    assert snapshot.detail is not None
    assert "droite" in snapshot.detail

    wait_until(
        lambda: not arduino.get_snapshot().backend_command.vibration_enabled,
        timeout_seconds=4.0,
    )
  finally:
    manager.stop_session("session-writing")
