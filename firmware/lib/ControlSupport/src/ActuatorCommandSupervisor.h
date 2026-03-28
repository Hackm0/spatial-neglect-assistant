#pragma once

#include "MillisTimeout.h"
#include "ProtocolTypes.h"

class ActuatorCommandSupervisor {
 public:
  ActuatorCommandSupervisor(float neutralServoAngleDegrees,
                            unsigned long commandTimeoutMs);

  void acceptCommand(const ActuatorCommand& command, unsigned long nowMs);
  ActuatorCommand currentCommand(unsigned long nowMs) const;
  bool isFailsafeActive(unsigned long nowMs) const;

 private:
  ActuatorCommand failsafeCommand_;
  ActuatorCommand lastCommand_;
  MillisTimeout commandTimeout_;
};
