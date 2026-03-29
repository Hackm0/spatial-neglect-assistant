#pragma once

#include "ProtocolTypes.h"

class AutonomousVibrationController {
 public:
  bool update(const SensorSnapshot& snapshot, unsigned long nowMs);

 private:
  static constexpr uint8_t kRequiredMotionSamples = 2U;

  static bool isInProximityZone(const SensorSnapshot& snapshot);
  static bool isMotionDetected(const SensorSnapshot& snapshot);

  bool burstActive_ = false;
  bool zoneServed_ = false;
  uint8_t consecutiveMotionSamples_ = 0U;
  unsigned long burstStartedAtMs_ = 0UL;
};
