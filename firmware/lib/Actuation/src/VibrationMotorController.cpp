#include "VibrationMotorController.h"

namespace {

constexpr unsigned long kMaxContinuousOnMs = 250UL;

}  // namespace

VibrationMotorController::VibrationMotorController(
    const VibrationMotorConfig& config)
    : config_(config),
      enabled_(false),
      initialized_(false),
      requestActive_(false),
      safetyLatched_(false),
      startedAtMs_(0UL) {}

void VibrationMotorController::begin() {
  pinMode(config_.pin, OUTPUT);
  initialized_ = true;
  requestActive_ = false;
  safetyLatched_ = false;
  startedAtMs_ = 0UL;
  writeOutput(false);
}

void VibrationMotorController::update(const unsigned long nowMs) {
  if (!enabled_ || !requestActive_ || safetyLatched_) {
    return;
  }

  if (kMaxContinuousOnMs > 0UL && nowMs - startedAtMs_ >= kMaxContinuousOnMs) {
    safetyLatched_ = true;
    writeOutput(false);
  }
}

void VibrationMotorController::turnOn() {
  setEnabled(true);
}

void VibrationMotorController::turnOff() {
  setEnabled(false);
}

void VibrationMotorController::setEnabled(const bool enabled) {
  if (!enabled) {
    requestActive_ = false;
    safetyLatched_ = false;
    startedAtMs_ = 0UL;
    if (enabled_ == enabled && initialized_) {
      return;
    }

    writeOutput(false);
    return;
  }

  if (!requestActive_) {
    requestActive_ = true;
    safetyLatched_ = false;
    startedAtMs_ = millis();
  }

  if (safetyLatched_) {
    if (enabled_) {
      writeOutput(false);
    }
    return;
  }

  if (enabled_ == enabled && initialized_) {
    return;
  }

  writeOutput(true);
}

bool VibrationMotorController::isEnabled() const {
  return enabled_;
}

uint8_t VibrationMotorController::outputLevelForState(const bool enabled) const {
  const bool shouldDriveHigh = config_.activeHigh ? enabled : !enabled;
  return shouldDriveHigh ? HIGH : LOW;
}

void VibrationMotorController::writeOutput(const bool enabled) {
  enabled_ = enabled;

  if (!initialized_) {
    return;
  }

  digitalWrite(config_.pin, outputLevelForState(enabled_));
}
