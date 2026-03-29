#include "AutonomousVibrationController.h"

#include <math.h>

namespace {

constexpr uint16_t kProximityTriggerMm = 600U;
constexpr float kMotionBaselineMilliG = 1000.0F;
constexpr float kMotionThresholdMilliG = 150.0F;
constexpr unsigned long kBurstDurationMs = 3000UL;
constexpr unsigned long kPulsePeriodMs = 1000UL;
constexpr unsigned long kPulseOnTimeMs = 150UL;

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

  const float accelX = static_cast<float>(snapshot.accelXMilliG);
  const float accelY = static_cast<float>(snapshot.accelYMilliG);
  const float accelZ = static_cast<float>(snapshot.accelZMilliG);
  const float magnitudeMilliG =
      sqrtf(accelX * accelX + accelY * accelY + accelZ * accelZ);
  const float deviationMilliG = fabsf(magnitudeMilliG - kMotionBaselineMilliG);
  return deviationMilliG >= kMotionThresholdMilliG;
}
