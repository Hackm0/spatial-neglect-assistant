#pragma once

#include <Arduino.h>

struct HcSr04Config {
  uint8_t triggerPin;
  uint8_t echoPin;
  unsigned long measurementIntervalMs;
  unsigned long echoTimeoutUs;
};

class HcSr04DistanceSensor {
 public:
  explicit HcSr04DistanceSensor(const HcSr04Config& config);

  bool begin();
  bool update();

  bool hasReading() const;
  bool lastMeasurementTimedOut() const;
  float distanceCm() const;

 private:
  enum class MeasurementState : uint8_t {
    kIdle,
    kTriggerHigh,
    kWaitingForEchoStart,
    kWaitingForEchoEnd,
  };

  bool isConfigurationValid() const;
  void startMeasurement(unsigned long nowMs, unsigned long nowUs);
  void finishSuccessfulMeasurement(unsigned long nowUs);
  void finishTimedOutMeasurement();

  const HcSr04Config config_;
  MeasurementState state_;
  float distanceCm_;
  unsigned long lastMeasurementStartMs_;
  unsigned long stateStartedUs_;
  unsigned long echoPulseStartedUs_;
  bool initialized_;
  bool hasReading_;
  bool lastMeasurementTimedOut_;
};
