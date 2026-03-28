#pragma once

#include <Arduino.h>

struct VibrationMotorConfig {
  uint8_t pin;
  bool activeHigh;
};

class VibrationMotorController {
 public:
  explicit VibrationMotorController(const VibrationMotorConfig& config);

  void begin();
  void turnOn();
  void turnOff();
  void setEnabled(bool enabled);

  bool isEnabled() const;

 private:
  uint8_t outputLevelForState(bool enabled) const;
  void writeOutput(bool enabled);

  const VibrationMotorConfig config_;
  bool enabled_;
  bool initialized_;
};
