#include "AnalogJoystick.h"

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

}  // namespace

AnalogJoystick::AnalogJoystick(const AnalogJoystickConfig& config)
    : config_(config), position_{0.0F, 0.0F}, buttonPressed_(false) {}

void AnalogJoystick::begin() {
  pinMode(config_.xPin, INPUT);
  pinMode(config_.yPin, INPUT);
  pinMode(config_.buttonPin,
          config_.buttonUsesPullup ? INPUT_PULLUP : INPUT);

  position_ = {0.0F, 0.0F};
  buttonPressed_ = false;

  // Prime the ADC multiplexer so the first real sample is less likely to be stale.
  static_cast<void>(readAxis(config_.xPin));
  static_cast<void>(readAxis(config_.yPin));
}

void AnalogJoystick::refresh() {
  position_.x = normalizeAxisReading(readAxis(config_.xPin), config_.invertX);
  position_.y = normalizeAxisReading(readAxis(config_.yPin), config_.invertY);

  const int buttonState = digitalRead(config_.buttonPin);
  buttonPressed_ =
      config_.buttonUsesPullup ? (buttonState == LOW) : (buttonState == HIGH);
}

JoystickPosition AnalogJoystick::getPosition() const {
  return position_;
}

float AnalogJoystick::getX() const {
  return position_.x;
}

float AnalogJoystick::getY() const {
  return position_.y;
}

bool AnalogJoystick::isButtonPressed() const {
  return buttonPressed_;
}

float AnalogJoystick::applyDeadzone(const float value) const {
  return fabsf(value) <= effectiveDeadzone() ? 0.0F : value;
}

float AnalogJoystick::effectiveDeadzone() const {
  return clampFloat(config_.deadzone, 0.0F, 1.0F);
}

int AnalogJoystick::readAxis(const uint8_t pin) const {
  static_cast<void>(analogRead(pin));
  return analogRead(pin);
}

float AnalogJoystick::normalizeAxisReading(const int rawReading,
                                           const bool invertAxis) const {
  const int clampedReading =
      constrain(rawReading, kAxisMinimumReading, kAxisMaximumReading);

  float normalizedValue = 0.0F;

  // Normalize each half of the range independently so both directions
  // reach their full scale around the configured center.
  if (clampedReading < kAxisCenterReading) {
    const float negativeSpan =
        static_cast<float>(kAxisCenterReading - kAxisMinimumReading);
    if (negativeSpan > 0.0F) {
      normalizedValue =
          static_cast<float>(clampedReading - kAxisCenterReading) / negativeSpan;
    }
  } else if (clampedReading > kAxisCenterReading) {
    const float positiveSpan =
        static_cast<float>(kAxisMaximumReading - kAxisCenterReading);
    if (positiveSpan > 0.0F) {
      normalizedValue =
          static_cast<float>(clampedReading - kAxisCenterReading) / positiveSpan;
    }
  }

  normalizedValue = clampFloat(normalizedValue, kMinimumNormalizedValue,
                               kMaximumNormalizedValue);
  if (invertAxis) {
    normalizedValue = -normalizedValue;
  }

  return applyDeadzone(normalizedValue);
}
