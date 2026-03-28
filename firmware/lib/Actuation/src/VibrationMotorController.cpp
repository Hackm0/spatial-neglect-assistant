#include "VibrationMotorController.h"

VibrationMotorController::VibrationMotorController(
    const VibrationMotorConfig& config)
    : config_(config), enabled_(false), initialized_(false) {}

void VibrationMotorController::begin() {
  pinMode(config_.pin, OUTPUT);
  initialized_ = true;
  writeOutput(false);
}

void VibrationMotorController::turnOn() {
  setEnabled(true);
}

void VibrationMotorController::turnOff() {
  setEnabled(false);
}

void VibrationMotorController::setEnabled(const bool enabled) {
  if (enabled_ == enabled && initialized_) {
    return;
  }

  writeOutput(enabled);
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
