#include <Arduino.h>
#include <FirmwareApplication.h>
#include <HardwareSerialByteStream.h>

namespace {

constexpr uint8_t kDistanceTriggerPin = 7U;
constexpr uint8_t kDistanceEchoPin = 8U;
constexpr uint8_t kJoystickXPin = A0;
constexpr uint8_t kJoystickYPin = A1;
constexpr uint8_t kJoystickButtonPin = 2U;
constexpr uint8_t kServoPin = 9U;
constexpr uint8_t kVibrationMotorPin = 10U;
constexpr uint8_t kLedPins[] = {3U, 5U, 11U, 4U, 6U};

constexpr float kServoMinAngle = 0.0F;
constexpr float kServoMaxAngle = 180.0F;
constexpr float kServoInitialAngle = 90.0F;
constexpr float kServoMaxDegreesPerSecond = 90.0F;

constexpr unsigned long kSerialBaudRate = 9600UL;
constexpr unsigned long kDistanceMeasurementIntervalMs = 60UL;
constexpr unsigned long kDistanceEchoTimeoutUs = 30000UL;
constexpr unsigned long kSensorSampleIntervalMs = 20UL;
constexpr unsigned long kTelemetryIntervalMs = 50UL;
// Allow transient link dropouts without immediately forcing failsafe.
constexpr unsigned long kCommandTimeoutMs = 10000UL;
constexpr unsigned long kAccelerometerRetryIntervalMs = 1000UL;

ProtocolConfig makeProtocolConfig() {
  ProtocolConfig config;
  config.primaryBaudRate = kSerialBaudRate;
  config.secondaryBaudRate = kSerialBaudRate;
  config.telemetryIntervalMs = kTelemetryIntervalMs;
  config.secondaryTelemetryIntervalMs = kTelemetryIntervalMs;
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

const LedScaleConfig kLedScaleConfig = {
  kLedPins,
  static_cast<uint8_t>(sizeof(kLedPins) / sizeof(kLedPins[0])),
  true};

const ProtocolConfig kProtocolConfig = makeProtocolConfig();

const FirmwareApplicationConfig kFirmwareConfig = {kDistanceSensorConfig,
                                                   kJoystickConfig,
                                                   kServoConfig,
                                                   kVibrationMotorConfig,
                                                   kLedScaleConfig,
                                                   kProtocolConfig,
                                                   kSensorSampleIntervalMs,
                                                   kAccelerometerRetryIntervalMs};

HardwareSerialByteStream serialTransport(Serial);
FirmwareApplication firmwareApplication(kFirmwareConfig, serialTransport);

}  // namespace

void setup() {
  static_cast<void>(firmwareApplication.begin());
}

void loop() {
  firmwareApplication.update(millis());
}
