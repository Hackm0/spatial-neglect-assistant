#include "ActuatorCommandSupervisor.h"

ActuatorCommandSupervisor::ActuatorCommandSupervisor(
    const float neutralServoAngleDegrees, const unsigned long commandTimeoutMs,
    const unsigned long vibrationMaxOnMs)
    : failsafeCommand_(),
      lastCommand_(),
      commandTimeout_(commandTimeoutMs),
      vibrationMaxOnMs_(vibrationMaxOnMs),
      vibrationStartedAtMs_(0UL),
      vibrationRequestActive_(false),
      vibrationSafetyLatched_(false) {
  failsafeCommand_.servoAngleDegrees = neutralServoAngleDegrees;
  failsafeCommand_.vibrationEnabled = false;
  lastCommand_ = failsafeCommand_;
}

void ActuatorCommandSupervisor::acceptCommand(const ActuatorCommand& command,
                                              const unsigned long nowMs) {
  lastCommand_ = command;
  if (command.vibrationEnabled) {
    if (!vibrationRequestActive_) {
      vibrationRequestActive_ = true;
      vibrationSafetyLatched_ = false;
      vibrationStartedAtMs_ = nowMs;
    }
  } else {
    vibrationRequestActive_ = false;
    vibrationSafetyLatched_ = false;
    vibrationStartedAtMs_ = 0UL;
  }
  commandTimeout_.start(nowMs);
}

ActuatorCommand ActuatorCommandSupervisor::currentCommand(
    const unsigned long nowMs) {
  if (isFailsafeActive(nowMs)) {
    return failsafeCommand_;
  }

  ActuatorCommand command = lastCommand_;
  if (!command.vibrationEnabled) {
    return command;
  }

  if (vibrationSafetyLatched_) {
    command.vibrationEnabled = false;
    return command;
  }

  if (vibrationMaxOnMs_ > 0UL &&
      nowMs - vibrationStartedAtMs_ >= vibrationMaxOnMs_) {
    vibrationSafetyLatched_ = true;
    command.vibrationEnabled = false;
    return command;
  }

  return command;
}

bool ActuatorCommandSupervisor::isFailsafeActive(
    const unsigned long nowMs) const {
  return !commandTimeout_.isActive() || commandTimeout_.isExpired(nowMs);
}
