#include <Arduino.h>
#include <HcSr04DistanceSensor.h>
#include <MillisInterval.h>
#include <Mpu9250Accelerometer.h>
#include <ServoMotorController.h>

namespace {

constexpr uint8_t kDistanceTriggerPin = 7U;
constexpr uint8_t kDistanceEchoPin = 8U;
constexpr uint8_t kServoPin = 9U;
constexpr float kServoMinAngle = 0.0F;
constexpr float kServoMaxAngle = 180.0F;
constexpr float kServoInitialAngle = 90.0F;
constexpr float kServoMaxDegreesPerSecond = 90.0F;
constexpr float kMinControlAccelerationG = -1.0F;
constexpr float kMaxControlAccelerationG = 1.0F;

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

const ServoMotorConfig kServoConfig = {
    kServoPin,
    kServoMinAngle,
    kServoMaxAngle,
    kServoInitialAngle,
    kServoMaxDegreesPerSecond,
};

Mpu9250Accelerometer accelerometer;
HcSr04DistanceSensor distanceSensor(kDistanceSensorConfig);
ServoMotorController servoMotor(kServoConfig);
MillisInterval sensorSampleInterval(kSensorSampleIntervalMs);
MillisInterval printInterval(kPrintIntervalMs);
MillisInterval initRetryInterval(kInitRetryIntervalMs);

float clampFloat(const float value, const float minimum, const float maximum) {
  if (value < minimum) {
    return minimum;
  }

  if (value > maximum) {
    return maximum;
  }

  return value;
}

float mapAccelXToServoAngle(const float accelX) {
  const float clampedAccelX =
      clampFloat(accelX, kMinControlAccelerationG, kMaxControlAccelerationG);
  const float normalizedAccelX =
      (clampedAccelX - kMinControlAccelerationG) /
      (kMaxControlAccelerationG - kMinControlAccelerationG);
  return kServoMinAngle +
         (normalizedAccelX * (kServoMaxAngle - kServoMinAngle));
}

void printDistanceValue() {
  Serial.print(F("Distance: "));

  if (distanceSensor.lastMeasurementTimedOut()) {
    Serial.println(F("timeout"));
    return;
  }

  if (!distanceSensor.hasReading()) {
    Serial.println(F("waiting"));
    return;
  }

  Serial.print(distanceSensor.distanceCm(), 2);
  Serial.println(F(" cm"));
}

void printSensorValues() {
  if (accelerometer.isInitialized()) {
    Serial.print(F("Accel X: "));
    Serial.print(accelerometer.getAccelX(), 3);
    Serial.print(F(" g | Y: "));
    Serial.print(accelerometer.getAccelY(), 3);
    Serial.print(F(" g | Z: "));
    Serial.print(accelerometer.getAccelZ(), 3);
    Serial.print(F(" g | "));
  } else {
    Serial.print(F("Accel: unavailable | "));
  }

  printDistanceValue();
}

void resetSensorCadence(const unsigned long nowMs) {
  sensorSampleInterval.reset(nowMs);
  printInterval.reset(nowMs);
}

void attemptAccelerometerInitialization(const unsigned long nowMs) {
  if (accelerometer.begin()) {
    resetSensorCadence(nowMs);
    Serial.println(F("MPU-9250 accelerometer initialized."));
    initRetryInterval.reset(nowMs);
    return;
  }

  Serial.println(F("MPU-9250 init failed. Retrying..."));
  initRetryInterval.reset(nowMs);
}

void refreshAccelerometerAndUpdateServoTarget() {
  if (!accelerometer.refresh()) {
    return;
  }

  servoMotor.setTargetAngle(mapAccelXToServoAngle(accelerometer.getAccelX()));
}

}  // namespace

void setup() {
  Serial.begin(115200);

  const unsigned long nowMs = millis();
  sensorSampleInterval.reset(nowMs);
  printInterval.reset(nowMs);
  initRetryInterval.reset(nowMs);
  if (!distanceSensor.begin()) {
    Serial.println(F("HC-SR04 initialization failed."));
  }
  if (!servoMotor.begin()) {
    Serial.println(F("Servo initialization failed."));
  }
  attemptAccelerometerInitialization(nowMs);
}

void loop() {
  const unsigned long nowMs = millis();

  servoMotor.update(nowMs);
  distanceSensor.update();

  if (!accelerometer.isInitialized()) {
    if (initRetryInterval.isReady(nowMs)) {
      attemptAccelerometerInitialization(nowMs);
    }
  } else if (sensorSampleInterval.isReady(nowMs)) {
    refreshAccelerometerAndUpdateServoTarget();
  }

  if (printInterval.isReady(nowMs)) {
    printSensorValues();
  }
}
