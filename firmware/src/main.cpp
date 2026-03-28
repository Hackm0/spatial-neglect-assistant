#include <Arduino.h>
#include <AnalogJoystick.h>
#include <HcSr04DistanceSensor.h>
#include <MillisInterval.h>
#include <Mpu9250Accelerometer.h>
#include <ServoMotorController.h>
#include <VibrationMotorController.h>

namespace {

constexpr uint8_t kDistanceTriggerPin = 7U;
constexpr uint8_t kDistanceEchoPin = 8U;
constexpr uint8_t kJoystickXPin = A0;
constexpr uint8_t kJoystickYPin = A1;
constexpr uint8_t kJoystickButtonPin = 2U;
constexpr uint8_t kServoPin = 9U;
constexpr uint8_t kVibrationMotorPin = 22U;
constexpr float kServoMinAngle = 0.0F;
constexpr float kServoMaxAngle = 180.0F;
constexpr float kServoInitialAngle = 90.0F;
constexpr float kServoMaxDegreesPerSecond = 90.0F;
constexpr float kVibrationActivationDistanceCm = 30.0F;
constexpr float kMinControlInput = -1.0F;
constexpr float kMaxControlInput = 1.0F;

constexpr unsigned long kDistanceMeasurementIntervalMs = 60UL;
constexpr unsigned long kDistanceEchoTimeoutUs = 30000UL;
constexpr unsigned long kPrintIntervalMs = 100UL;
constexpr unsigned long kSensorSampleIntervalMs = 20UL;
constexpr unsigned long kInitRetryIntervalMs = 1000UL;

const HcSr04Config kDistanceSensorConfig = {
    kDistanceTriggerPin,
    kDistanceEchoPin,
    kDistanceMeasurementIntervalMs,
    kDistanceEchoTimeoutUs,
};

const AnalogJoystickConfig kJoystickConfig = {
    kJoystickXPin,
    kJoystickYPin,
    kJoystickButtonPin,
    0.05F,
    false,
    false,
    true,
};

const ServoMotorConfig kServoConfig = {
    kServoPin,
    kServoMinAngle,
    kServoMaxAngle,
    kServoInitialAngle,
    kServoMaxDegreesPerSecond,
};

const VibrationMotorConfig kVibrationMotorConfig = {
    kVibrationMotorPin,
    true,
};

enum class DistanceReadingState : uint8_t {
  kUnavailable,
  kTimedOut,
  kValid,
};

Mpu9250Accelerometer accelerometer;
AnalogJoystick joystick(kJoystickConfig);
HcSr04DistanceSensor distanceSensor(kDistanceSensorConfig);
ServoMotorController servoMotor(kServoConfig);
VibrationMotorController vibrationMotor(kVibrationMotorConfig);
MillisInterval sensorSampleInterval(kSensorSampleIntervalMs);
MillisInterval printInterval(kPrintIntervalMs);
MillisInterval initRetryInterval(kInitRetryIntervalMs);
float latestAccelerometerX = 0.0F;
bool accelerometerReadingAvailable = false;

float clampFloat(const float value, const float minimum, const float maximum) {
  if (value < minimum) {
    return minimum;
  }

  if (value > maximum) {
    return maximum;
  }

  return value;
}

float mapControlInputToServoAngle(const float controlInput) {
  const float clampedControlInput =
      clampFloat(controlInput, kMinControlInput, kMaxControlInput);
  const float normalizedControlInput =
      (clampedControlInput - kMinControlInput) /
      (kMaxControlInput - kMinControlInput);
  return kServoMinAngle +
         (normalizedControlInput * (kServoMaxAngle - kServoMinAngle));
}

float combineControlInputs(const float joystickX,
                           const bool hasAccelerometerSample,
                           const float accelX) {
  if (!hasAccelerometerSample) {
    return joystickX;
  }

  const float clampedAccelX =
      clampFloat(accelX, kMinControlInput, kMaxControlInput);
  return (clampedAccelX + joystickX) * 0.5F;
}

DistanceReadingState currentDistanceReadingState() {
  if (distanceSensor.lastMeasurementTimedOut()) {
    return DistanceReadingState::kTimedOut;
  }

  if (!distanceSensor.hasReading()) {
    return DistanceReadingState::kUnavailable;
  }

  return DistanceReadingState::kValid;
}

void updateVibrationMotorFromDistanceSensor() {
  if (currentDistanceReadingState() != DistanceReadingState::kValid) {
    vibrationMotor.turnOff();
    return;
  }

  vibrationMotor.setEnabled(
      distanceSensor.distanceCm() < kVibrationActivationDistanceCm);
}

void printTelemetry() {
  Serial.print(F("Joystick X: "));
  Serial.print(joystick.getX(), 3);
  Serial.print(F(" | Y: "));
  Serial.print(joystick.getY(), 3);
  Serial.print(F(" | Button: "));
  Serial.print(joystick.isButtonPressed() ? F("pressed") : F("released"));
  Serial.print(F(" | Accel X: "));

  if (accelerometerReadingAvailable) {
    Serial.print(latestAccelerometerX, 3);
    Serial.print(F(" g"));
  } else {
    Serial.print(F("unavailable"));
  }

  Serial.print(F(" | Servo target: "));
  Serial.print(servoMotor.targetAngle(), 1);
  Serial.print(F(" | Distance: "));

  switch (currentDistanceReadingState()) {
    case DistanceReadingState::kUnavailable:
      Serial.println(F("unavailable"));
      return;

    case DistanceReadingState::kTimedOut:
      Serial.println(F("timeout"));
      return;

    case DistanceReadingState::kValid:
      Serial.print(distanceSensor.distanceCm(), 1);
      Serial.println(F(" cm"));
      return;
  }
}

void resetSensorCadence(const unsigned long nowMs) {
  sensorSampleInterval.reset(nowMs);
  printInterval.reset(nowMs);
}

void attemptAccelerometerInitialization(const unsigned long nowMs) {
  if (accelerometer.begin()) {
    accelerometerReadingAvailable = false;
    resetSensorCadence(nowMs);
    Serial.println(F("MPU-9250 accelerometer initialized."));
    initRetryInterval.reset(nowMs);
    return;
  }

  accelerometerReadingAvailable = false;
  Serial.println(F("MPU-9250 init failed. Retrying..."));
  initRetryInterval.reset(nowMs);
}

void refreshSensorsAndUpdateServoTarget() {
  joystick.refresh();

  accelerometerReadingAvailable = false;
  if (accelerometer.isInitialized() && accelerometer.refresh()) {
    latestAccelerometerX = accelerometer.getAccelX();
    accelerometerReadingAvailable = true;
  }

  const float controlInput = combineControlInputs(
      joystick.getX(), accelerometerReadingAvailable, latestAccelerometerX);
  servoMotor.setTargetAngle(mapControlInputToServoAngle(controlInput));
}

}  // namespace

void setup() {
  Serial.begin(115200);

  const unsigned long nowMs = millis();
  sensorSampleInterval.reset(nowMs);
  printInterval.reset(nowMs);
  initRetryInterval.reset(nowMs);
  joystick.begin();
  if (!distanceSensor.begin()) {
    Serial.println(F("HC-SR04 initialization failed."));
  }
  if (!servoMotor.begin()) {
    Serial.println(F("Servo initialization failed."));
  }
  vibrationMotor.begin();
  attemptAccelerometerInitialization(nowMs);
}

void loop() {
  const unsigned long nowMs = millis();

  servoMotor.update(nowMs);
  const bool distanceMeasurementCompleted = distanceSensor.update();

  if (distanceMeasurementCompleted) {
    updateVibrationMotorFromDistanceSensor();
  }

  if (!accelerometer.isInitialized()) {
    if (initRetryInterval.isReady(nowMs)) {
      attemptAccelerometerInitialization(nowMs);
    }
  }

  if (sensorSampleInterval.isReady(nowMs)) {
    refreshSensorsAndUpdateServoTarget();
  }

  if (printInterval.isReady(nowMs)) {
    printTelemetry();
  }
}
