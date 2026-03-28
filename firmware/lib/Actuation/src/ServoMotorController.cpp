#include "ServoMotorController.h"

#include <math.h>

namespace {

float clampFloat(const float value, const float minimum, const float maximum) {
  if (value < minimum) {
    return minimum;
  }

  if (value > maximum) {
    return maximum;
  }

  return value;
}

int roundToNearestInt(const float value) {
  return static_cast<int>(value + (value >= 0.0F ? 0.5F : -0.5F));
}

}  // namespace

ServoMotorController::ServoMotorController(const ServoMotorConfig& config)
    : config_(config),
      currentAngle_(0.0F),
      targetAngle_(0.0F),
      lastUpdateMs_(0UL),
      hasLastUpdateTime_(false),
      attached_(false) {
  currentAngle_ = clampAngle(config_.initialAngle);
  targetAngle_ = currentAngle_;
}

bool ServoMotorController::begin() {
  if (!attached_) {
    servo_.attach(config_.pin);
    attached_ = servo_.attached();
  }

  if (!attached_) {
    return false;
  }

  currentAngle_ = clampAngle(config_.initialAngle);
  targetAngle_ = currentAngle_;
  hasLastUpdateTime_ = false;
  writeCurrentAngle();
  return true;
}

void ServoMotorController::setTargetAngle(const float angle) {
  targetAngle_ = clampAngle(angle);
}

void ServoMotorController::setImmediateAngle(const float angle) {
  currentAngle_ = clampAngle(angle);
  targetAngle_ = currentAngle_;
  hasLastUpdateTime_ = false;
  writeCurrentAngle();
}

void ServoMotorController::update(const unsigned long nowMs) {
  if (!attached_) {
    return;
  }

  if (!hasLastUpdateTime_) {
    lastUpdateMs_ = nowMs;
    hasLastUpdateTime_ = true;
    return;
  }

  const unsigned long elapsedMs = nowMs - lastUpdateMs_;
  lastUpdateMs_ = nowMs;

  if (elapsedMs == 0UL) {
    return;
  }

  const float remainingAngle = targetAngle_ - currentAngle_;
  if (fabsf(remainingAngle) <= kAngleEpsilon) {
    currentAngle_ = targetAngle_;
    return;
  }

  const float maxStep =
      effectiveMaxDegreesPerSecond() * (static_cast<float>(elapsedMs) / 1000.0F);
  if (maxStep <= 0.0F) {
    return;
  }

  if (fabsf(remainingAngle) <= maxStep) {
    currentAngle_ = targetAngle_;
  } else if (remainingAngle > 0.0F) {
    currentAngle_ += maxStep;
  } else {
    currentAngle_ -= maxStep;
  }

  writeCurrentAngle();
}

float ServoMotorController::currentAngle() const {
  return currentAngle_;
}

float ServoMotorController::targetAngle() const {
  return targetAngle_;
}

bool ServoMotorController::isAttached() const {
  return attached_;
}

float ServoMotorController::clampAngle(const float angle) const {
  return clampFloat(angle, effectiveMinAngle(), effectiveMaxAngle());
}

float ServoMotorController::effectiveMinAngle() const {
  return config_.minAngle < config_.maxAngle ? config_.minAngle
                                             : config_.maxAngle;
}

float ServoMotorController::effectiveMaxAngle() const {
  return config_.minAngle < config_.maxAngle ? config_.maxAngle
                                             : config_.minAngle;
}

float ServoMotorController::effectiveMaxDegreesPerSecond() const {
  return config_.maxDegreesPerSecond > 0.0F ? config_.maxDegreesPerSecond
                                            : 0.0F;
}

void ServoMotorController::writeCurrentAngle() {
  if (!attached_) {
    return;
  }

  currentAngle_ = clampAngle(currentAngle_);
  servo_.write(roundToNearestInt(currentAngle_));
}
