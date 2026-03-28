#include <Arduino.h>
#include <MillisInterval.h>
#include <Mpu9250Accelerometer.h>

namespace {

constexpr unsigned long kPrintIntervalMs = 100UL;
constexpr unsigned long kInitRetryIntervalMs = 1000UL;

Mpu9250Accelerometer accelerometer;
MillisInterval printInterval(kPrintIntervalMs);
MillisInterval initRetryInterval(kInitRetryIntervalMs);

void printAccelerationValues() {
  Serial.print(F("Accel X: "));
  Serial.print(accelerometer.getAccelX(), 3);
  Serial.print(F(" g | Y: "));
  Serial.print(accelerometer.getAccelY(), 3);
  Serial.print(F(" g | Z: "));
  Serial.print(accelerometer.getAccelZ(), 3);
  Serial.println(F(" g"));
}

void attemptAccelerometerInitialization(const unsigned long nowMs) {
  if (accelerometer.begin()) {
    Serial.println(F("MPU-9250 accelerometer initialized."));
    initRetryInterval.reset(nowMs);
    return;
  }

  Serial.println(F("MPU-9250 init failed. Retrying..."));
  initRetryInterval.reset(nowMs);
}

}  // namespace

void setup() {
  Serial.begin(115200);

  const unsigned long nowMs = millis();
  printInterval.reset(nowMs);
  initRetryInterval.reset(nowMs);
  attemptAccelerometerInitialization(nowMs);
}

void loop() {
  const unsigned long nowMs = millis();

  if (!accelerometer.isInitialized()) {
    if (initRetryInterval.isReady(nowMs)) {
      attemptAccelerometerInitialization(nowMs);
    }
    return;
  }

  if (!printInterval.isReady(nowMs)) {
    return;
  }

  if (!accelerometer.refresh()) {
    Serial.println(F("MPU-9250 read failed."));
    return;
  }

  printAccelerationValues();
}
