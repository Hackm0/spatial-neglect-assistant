#include "Mpu9250Accelerometer.h"

Mpu9250Accelerometer::Mpu9250Accelerometer(TwoWire& wire,
                                           const uint8_t address)
    : wire_(wire),
      sensor_(address),
      accelX_(0.0F),
      accelY_(0.0F),
      accelZ_(0.0F),
      initialized_(false) {}

bool Mpu9250Accelerometer::begin() {
  initialized_ = false;

  wire_.begin();
  sensor_.setWire(&wire_);

  uint8_t sensorId = 0U;
  if (sensor_.readId(&sensorId) != 0U || !isSensorIdValid(sensorId)) {
    return false;
  }

  sensor_.beginAccel(ACC_FULL_SCALE_4_G);
  initialized_ = true;
  return true;
}

bool Mpu9250Accelerometer::refresh() {
  if (!initialized_ || sensor_.accelUpdate() != 0U) {
    return false;
  }

  accelX_ = sensor_.accelX();
  accelY_ = sensor_.accelY();
  accelZ_ = sensor_.accelZ();
  return true;
}

float Mpu9250Accelerometer::getAccelX() const {
  return accelX_;
}

float Mpu9250Accelerometer::getAccelY() const {
  return accelY_;
}

float Mpu9250Accelerometer::getAccelZ() const {
  return accelZ_;
}

bool Mpu9250Accelerometer::isInitialized() const {
  return initialized_;
}

bool Mpu9250Accelerometer::isSensorIdValid(const uint8_t sensorId) const {
  return sensorId == kMpu6500CompatibleDeviceId ||
         sensorId == kPrimaryExpectedDeviceId ||
         sensorId == kSecondaryCompatibleDeviceId;
}
