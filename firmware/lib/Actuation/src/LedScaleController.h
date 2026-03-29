#pragma once

#include <Arduino.h>

struct LedScaleConfig {
  const uint8_t* pins;
  uint8_t pinCount;
  bool activeHigh;
};

class LedScaleController {
 public:
  explicit LedScaleController(const LedScaleConfig& config);

  void begin();
  void setScalePermille(int16_t valuePermille);
  uint8_t activeLedCount() const;

 private:
  static int16_t clampPermille(int16_t value);
  uint8_t computeActiveLedCount(int16_t valuePermille) const;
  uint8_t outputLevelForState(bool enabled) const;
  void applyOutputs();

  const LedScaleConfig config_;
  uint8_t activeLedCount_;
  bool initialized_;
};
