#pragma once

#include <Arduino.h>

struct AnalogJoystickConfig {
  uint8_t xPin;
  uint8_t yPin;
  uint8_t buttonPin;
  float deadzone;
  bool invertX;
  bool invertY;
  bool buttonUsesPullup;

  AnalogJoystickConfig(uint8_t xPinValue,
                       uint8_t yPinValue,
                       uint8_t buttonPinValue,
                       float deadzoneValue = 0.05F,
                       bool invertXValue = false,
                       bool invertYValue = false,
                       bool buttonUsesPullupValue = true)
      : xPin(xPinValue),
        yPin(yPinValue),
        buttonPin(buttonPinValue),
        deadzone(deadzoneValue),
        invertX(invertXValue),
        invertY(invertYValue),
        buttonUsesPullup(buttonUsesPullupValue) {}
};

struct JoystickPosition {
  float x;
  float y;
};

class AnalogJoystick {
 public:
  explicit AnalogJoystick(const AnalogJoystickConfig& config);

  void begin();
  void refresh();

  JoystickPosition getPosition() const;
  float getX() const;
  float getY() const;
  bool isButtonPressed() const;

 private:
  static constexpr int kAxisMinimumReading = 0;
  static constexpr int kAxisCenterReading = 512;
  static constexpr int kAxisMaximumReading = 1023;
  static constexpr float kMinimumNormalizedValue = -1.0F;
  static constexpr float kMaximumNormalizedValue = 1.0F;

  float applyDeadzone(float value) const;
  float effectiveDeadzone() const;
  int readAxis(uint8_t pin) const;
  float normalizeAxisReading(int rawReading, bool invertAxis) const;

  const AnalogJoystickConfig config_;
  JoystickPosition position_;
  bool buttonPressed_;
};
