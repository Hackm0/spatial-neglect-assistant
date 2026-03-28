#include "HcSr04DistanceSensor.h"

namespace {

constexpr unsigned long kTriggerPulseWidthUs = 10UL;
constexpr float kDistancePerMicrosecondCm = 0.0343F;
constexpr float kRoundTripDivisor = 2.0F;

float pulseDurationToDistanceCm(const unsigned long pulseDurationUs) {
  return (static_cast<float>(pulseDurationUs) * kDistancePerMicrosecondCm) /
         kRoundTripDivisor;
}

}  // namespace

HcSr04DistanceSensor::HcSr04DistanceSensor(const HcSr04Config& config)
    : config_(config),
      state_(MeasurementState::kIdle),
      distanceCm_(0.0F),
      lastMeasurementStartMs_(0UL),
      stateStartedUs_(0UL),
      echoPulseStartedUs_(0UL),
      initialized_(false),
      hasReading_(false),
      lastMeasurementTimedOut_(false) {}

bool HcSr04DistanceSensor::begin() {
  if (!isConfigurationValid()) {
    initialized_ = false;
    return false;
  }

  pinMode(config_.triggerPin, OUTPUT);
  digitalWrite(config_.triggerPin, LOW);
  pinMode(config_.echoPin, INPUT);

  state_ = MeasurementState::kIdle;
  distanceCm_ = 0.0F;
  lastMeasurementStartMs_ = millis() - config_.measurementIntervalMs;
  stateStartedUs_ = 0UL;
  echoPulseStartedUs_ = 0UL;
  initialized_ = true;
  hasReading_ = false;
  lastMeasurementTimedOut_ = false;
  return true;
}

bool HcSr04DistanceSensor::update() {
  if (!initialized_) {
    return false;
  }

  const unsigned long nowUs = micros();
  const unsigned long nowMs = millis();

  switch (state_) {
    case MeasurementState::kIdle:
      if ((nowMs - lastMeasurementStartMs_) < config_.measurementIntervalMs) {
        return false;
      }

      startMeasurement(nowMs, nowUs);
      return false;

    case MeasurementState::kTriggerHigh:
      if ((nowUs - stateStartedUs_) < kTriggerPulseWidthUs) {
        return false;
      }

      digitalWrite(config_.triggerPin, LOW);
      state_ = MeasurementState::kWaitingForEchoStart;
      stateStartedUs_ = nowUs;
      return false;

    case MeasurementState::kWaitingForEchoStart:
      if (digitalRead(config_.echoPin) == HIGH) {
        echoPulseStartedUs_ = nowUs;
        state_ = MeasurementState::kWaitingForEchoEnd;
        return false;
      }

      if ((nowUs - stateStartedUs_) >= config_.echoTimeoutUs) {
        finishTimedOutMeasurement();
        return true;
      }
      return false;

    case MeasurementState::kWaitingForEchoEnd:
      if (digitalRead(config_.echoPin) == LOW) {
        finishSuccessfulMeasurement(nowUs);
        return true;
      }

      if ((nowUs - echoPulseStartedUs_) >= config_.echoTimeoutUs) {
        finishTimedOutMeasurement();
        return true;
      }
      return false;
  }

  return false;
}

bool HcSr04DistanceSensor::hasReading() const {
  return hasReading_;
}

bool HcSr04DistanceSensor::lastMeasurementTimedOut() const {
  return lastMeasurementTimedOut_;
}

float HcSr04DistanceSensor::distanceCm() const {
  return distanceCm_;
}

bool HcSr04DistanceSensor::isConfigurationValid() const {
  return config_.triggerPin != config_.echoPin && config_.echoTimeoutUs > 0UL;
}

void HcSr04DistanceSensor::startMeasurement(const unsigned long nowMs,
                                            const unsigned long nowUs) {
  lastMeasurementStartMs_ = nowMs;
  stateStartedUs_ = nowUs;
  digitalWrite(config_.triggerPin, LOW);
  digitalWrite(config_.triggerPin, HIGH);
  state_ = MeasurementState::kTriggerHigh;
}

void HcSr04DistanceSensor::finishSuccessfulMeasurement(
    const unsigned long nowUs) {
  const unsigned long pulseDurationUs = nowUs - echoPulseStartedUs_;
  distanceCm_ = pulseDurationToDistanceCm(pulseDurationUs);
  state_ = MeasurementState::kIdle;
  hasReading_ = true;
  lastMeasurementTimedOut_ = false;
}

void HcSr04DistanceSensor::finishTimedOutMeasurement() {
  digitalWrite(config_.triggerPin, LOW);
  state_ = MeasurementState::kIdle;
  lastMeasurementTimedOut_ = true;
}
