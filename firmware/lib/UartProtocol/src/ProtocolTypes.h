#pragma once

#include <stdint.h>

struct ActuatorCommand {
  float servoAngleDegrees = 0.0F;
  bool vibrationEnabled = false;
};

struct SensorSnapshot {
  uint16_t distanceMm = 0U;
  bool distanceValid = false;
  bool distanceTimedOut = false;
  int16_t accelXMilliG = 0;
  int16_t accelYMilliG = 0;
  int16_t accelZMilliG = 0;
  bool accelValid = false;
  int16_t joystickXPermille = 0;
  int16_t joystickYPermille = 0;
  bool joystickButtonPressed = false;
};

struct ProtocolConfig {
  unsigned long primaryBaudRate = 9600UL;
  unsigned long secondaryBaudRate = 9600UL;
  unsigned long telemetryIntervalMs = 50UL;
  unsigned long secondaryTelemetryIntervalMs = 500UL;
  unsigned long commandTimeoutMs = 500UL;
};

enum class UartMessageType : uint8_t {
  kActuatorCommand = 0x01U,
  kTelemetrySnapshot = 0x81U,
};
