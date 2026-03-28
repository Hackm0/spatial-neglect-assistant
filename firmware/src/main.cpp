#include <Arduino.h>

#include <FirmwareApplication.h>

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

constexpr unsigned long kBaudRate = 115200UL;
constexpr unsigned long kDistanceMeasurementIntervalMs = 60UL;
constexpr unsigned long kDistanceEchoTimeoutUs = 30000UL;
constexpr unsigned long kSensorSampleIntervalMs = 20UL;
constexpr unsigned long kTelemetryIntervalMs = 50UL;
constexpr unsigned long kCommandTimeoutMs = 250UL;
constexpr unsigned long kAccelerometerRetryIntervalMs = 1000UL;

ProtocolConfig makeProtocolConfig() {
  ProtocolConfig config;
  config.baudRate = kBaudRate;
  config.telemetryIntervalMs = kTelemetryIntervalMs;
  config.commandTimeoutMs = kCommandTimeoutMs;
  return config;
}

const HcSr04Config kDistanceSensorConfig = {kDistanceTriggerPin,
                                            kDistanceEchoPin,
                                            kDistanceMeasurementIntervalMs,
                                            kDistanceEchoTimeoutUs};

const AnalogJoystickConfig kJoystickConfig = {kJoystickXPin, kJoystickYPin,
                                              kJoystickButtonPin, 0.05F, false,
                                              false, true};

const ServoMotorConfig kServoConfig = {kServoPin,
                                       kServoMinAngle,
                                       kServoMaxAngle,
                                       kServoInitialAngle,
                                       kServoMaxDegreesPerSecond};

const VibrationMotorConfig kVibrationMotorConfig = {kVibrationMotorPin, true};

const ProtocolConfig kProtocolConfig = makeProtocolConfig();

const FirmwareApplicationConfig kFirmwareConfig = {kDistanceSensorConfig,
                                                   kJoystickConfig,
                                                   kServoConfig,
                                                   kVibrationMotorConfig,
                                                   kProtocolConfig,
                                                   kSensorSampleIntervalMs,
                                                   kAccelerometerRetryIntervalMs};

FirmwareApplication firmwareApplication(kFirmwareConfig);

}  // namespace

void setup() {
  static_cast<void>(firmwareApplication.begin());
}

void loop() {
  firmwareApplication.update(millis());
}
