#include <Arduino.h>
#include <MillisInterval.h>
#include <Mpu9250Accelerometer.h>
#include <ServoMotorController.h>

namespace {

constexpr uint8_t kServoPin = 9U;
constexpr float kServoMinAngle = 0.0F;
constexpr float kServoMaxAngle = 180.0F;
constexpr float kServoInitialAngle = 90.0F;
constexpr float kServoMaxDegreesPerSecond = 90.0F;
constexpr float kMinControlAccelerationG = -1.0F;
constexpr float kMaxControlAccelerationG = 1.0F;

constexpr unsigned long kPrintIntervalMs = 100UL;
constexpr unsigned long kSensorSampleIntervalMs = 20UL;
constexpr unsigned long kInitRetryIntervalMs = 1000UL;

const ServoMotorConfig kServoConfig = {
    kServoPin,
    kServoMinAngle,
    kServoMaxAngle,
    kServoInitialAngle,
    kServoMaxDegreesPerSecond,
};

Mpu9250Accelerometer accelerometer;
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

void printAccelerationValues() {
  Serial.print(F("Accel X: "));
  Serial.print(accelerometer.getAccelX(), 3);
  Serial.print(F(" g | Y: "));
  Serial.print(accelerometer.getAccelY(), 3);
  Serial.print(F(" g | Z: "));
  Serial.print(accelerometer.getAccelZ(), 3);
  Serial.println(F(" g"));
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
  if (!servoMotor.begin()) {
    Serial.println(F("Servo initialization failed."));
  }
  attemptAccelerometerInitialization(nowMs);
}

void loop() {
  const unsigned long nowMs = millis();

  servoMotor.update(nowMs);

  if (!accelerometer.isInitialized()) {
    if (initRetryInterval.isReady(nowMs)) {
      attemptAccelerometerInitialization(nowMs);
    }
    return;
  }

  if (sensorSampleInterval.isReady(nowMs)) {
    refreshAccelerometerAndUpdateServoTarget();
  }

  if (printInterval.isReady(nowMs)) {
    printAccelerationValues();
  }
}
