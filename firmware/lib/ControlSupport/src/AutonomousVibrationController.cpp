#include "AutonomousVibrationController.h"

namespace {

constexpr uint16_t kProximityTriggerMm = 900U;
constexpr int16_t kMotionAxisThresholdMilliG = 180;
constexpr unsigned long kBurstDurationMs = 1500UL;
constexpr unsigned long kPulsePeriodMs = 1200UL;
constexpr unsigned long kPulseOnTimeMs = 100UL;

}  // namespace

bool AutonomousVibrationController::update(const SensorSnapshot& snapshot,
                                           const unsigned long nowMs) {
  const bool inZone = isInProximityZone(snapshot);

  if (!inZone) {
    zoneServed_ = false;
    consecutiveMotionSamples_ = 0U;
  }

  if (burstActive_) {
    const unsigned long burstElapsedMs = nowMs - burstStartedAtMs_;
    if (burstElapsedMs >= kBurstDurationMs) {
      burstActive_ = false;
      consecutiveMotionSamples_ = 0U;
      return false;
    }

    const unsigned long pulsePhaseMs = burstElapsedMs % kPulsePeriodMs;
    return pulsePhaseMs < kPulseOnTimeMs;
  }

  if (!inZone || zoneServed_) {
    return false;
  }

  if (!isMotionDetected(snapshot)) {
    consecutiveMotionSamples_ = 0U;
    return false;
  }

  if (consecutiveMotionSamples_ < kRequiredMotionSamples) {
    ++consecutiveMotionSamples_;
  }

  if (consecutiveMotionSamples_ < kRequiredMotionSamples) {
    return false;
  }

  burstActive_ = true;
  zoneServed_ = true;
  consecutiveMotionSamples_ = 0U;
  burstStartedAtMs_ = nowMs;
  return true;
}

bool AutonomousVibrationController::isInProximityZone(
    const SensorSnapshot& snapshot) {
  if (!snapshot.distanceValid || snapshot.distanceTimedOut) {
    return false;
  }

  if (snapshot.distanceMm == 0U) {
    return false;
  }

  return snapshot.distanceMm < kProximityTriggerMm;
}

bool AutonomousVibrationController::isMotionDetected(
    const SensorSnapshot& snapshot) {
  if (!snapshot.accelValid) {
    return false;
  }

  const int32_t accelXAbs = snapshot.accelXMilliG >= 0
                                ? static_cast<int32_t>(snapshot.accelXMilliG)
                                : -static_cast<int32_t>(snapshot.accelXMilliG);
  const int32_t accelZAbs = snapshot.accelZMilliG >= 0
                                ? static_cast<int32_t>(snapshot.accelZMilliG)
                                : -static_cast<int32_t>(snapshot.accelZMilliG);

  // Trigger motion if either horizontal axis exceeds the sensitivity threshold.
  return accelXAbs > kMotionAxisThresholdMilliG ||
         accelZAbs > kMotionAxisThresholdMilliG;
}
