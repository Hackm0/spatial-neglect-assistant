#include "ActuatorCommandSupervisor.h"

ActuatorCommandSupervisor::ActuatorCommandSupervisor(
    const float neutralServoAngleDegrees, const unsigned long commandTimeoutMs)
    : failsafeCommand_(), lastCommand_(), commandTimeout_(commandTimeoutMs) {
  failsafeCommand_.servoAngleDegrees = neutralServoAngleDegrees;
  failsafeCommand_.vibrationEnabled = false;
  lastCommand_ = failsafeCommand_;
}

void ActuatorCommandSupervisor::acceptCommand(const ActuatorCommand& command,
                                              const unsigned long nowMs) {
  lastCommand_ = command;
  commandTimeout_.start(nowMs);
}

ActuatorCommand ActuatorCommandSupervisor::currentCommand(
    const unsigned long nowMs) const {
  return isFailsafeActive(nowMs) ? failsafeCommand_ : lastCommand_;
}

bool ActuatorCommandSupervisor::isFailsafeActive(
    const unsigned long nowMs) const {
  return !commandTimeout_.isActive() || commandTimeout_.isExpired(nowMs);
}
