#pragma once

#include "MillisTimeout.h"
#include "ProtocolTypes.h"

class ActuatorCommandSupervisor {
 public:
  ActuatorCommandSupervisor(float neutralServoAngleDegrees,
                            unsigned long commandTimeoutMs,
                            unsigned long vibrationMaxOnMs = 150UL);

  void acceptCommand(const ActuatorCommand& command, unsigned long nowMs);
  ActuatorCommand currentCommand(unsigned long nowMs);
  bool isFailsafeActive(unsigned long nowMs) const;

 private:
  ActuatorCommand failsafeCommand_;
  ActuatorCommand lastCommand_;
  MillisTimeout commandTimeout_;
  unsigned long vibrationMaxOnMs_;
  unsigned long vibrationStartedAtMs_;
  bool vibrationRequestActive_;
  bool vibrationSafetyLatched_;
};
