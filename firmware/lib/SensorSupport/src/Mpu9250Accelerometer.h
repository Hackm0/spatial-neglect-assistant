#pragma once

#include <Arduino.h>
#include <MPU9250_asukiaaa.h>
#include <Wire.h>

class Mpu9250Accelerometer {
 public:
  explicit Mpu9250Accelerometer(
      TwoWire& wire = Wire,
      uint8_t address = MPU9250_ADDRESS_AD0_LOW);

  bool begin();
  bool refresh();

  float getAccelX() const;
  float getAccelY() const;
  float getAccelZ() const;

  bool isInitialized() const;

 private:
  static constexpr uint8_t kMpu6500CompatibleDeviceId = 0x70U;
  static constexpr uint8_t kPrimaryExpectedDeviceId = 0x71U;
  static constexpr uint8_t kSecondaryCompatibleDeviceId = 0x73U;

  bool isSensorIdValid(uint8_t sensorId) const;

  TwoWire& wire_;
  MPU9250_asukiaaa sensor_;
  float accelX_;
  float accelY_;
  float accelZ_;
  bool initialized_;
};
