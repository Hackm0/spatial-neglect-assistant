from __future__ import annotations

import time
from dataclasses import dataclass

from mobile_ingestion.object_feedback import ArduinoBurstFeedbackController
from uart_protocol import ActuatorCommand


def wait_until(predicate: object, *, timeout_seconds: float = 1.0) -> None:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.01)
  raise AssertionError("Timed out waiting for vibration feedback.")


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
  backend_command: ActuatorCommand


class FakeArduinoController:

  def __init__(self) -> None:
    self.backend_command = ActuatorCommand(90.0, False)
    self.commands: list[ActuatorCommand] = []

  def get_snapshot(self) -> FakeSnapshot:
    return FakeSnapshot(self.backend_command)

  def set_backend_command(self, command: ActuatorCommand) -> None:
    self.backend_command = command
    self.commands.append(command)


def test_burst_feedback_emits_short_vibration_pulses_and_stops() -> None:
  controller = FakeArduinoController()
  feedback = ArduinoBurstFeedbackController(
      controller=controller,
      burst_on_seconds=0.02,
      burst_period_seconds=0.05,
      burst_duration_seconds=0.18,
      tick_seconds=0.005,
  )

  feedback.start_session("session-one")
  feedback.notify_target_detected("session-one")

  wait_until(lambda: any(command.vibration_enabled for command in controller.commands))
  wait_until(
      lambda: any(not command.vibration_enabled for command in controller.commands[1:]),
      timeout_seconds=1.0,
  )
  wait_until(
      lambda: controller.backend_command.vibration_enabled is False,
      timeout_seconds=1.0,
  )
  feedback.shutdown()

  assert controller.commands[0].vibration_enabled is True
  assert controller.backend_command.vibration_enabled is False


def test_burst_feedback_stops_when_cleared() -> None:
  controller = FakeArduinoController()
  feedback = ArduinoBurstFeedbackController(
      controller=controller,
      burst_on_seconds=0.03,
      burst_period_seconds=0.08,
      burst_duration_seconds=0.5,
      tick_seconds=0.005,
  )

  feedback.start_session("session-one")
  feedback.notify_target_detected("session-one")
  wait_until(lambda: controller.backend_command.vibration_enabled is True)

  feedback.clear("session-one")
  wait_until(lambda: controller.backend_command.vibration_enabled is False)
  feedback.shutdown()

  assert controller.backend_command.vibration_enabled is False
