#include "FirmwareApplication.h"

#include <limits.h>

#include <NullByteStream.h>

namespace {

constexpr float kMinimumNormalizedInput = -1.0F;
constexpr float kMaximumNormalizedInput = 1.0F;
constexpr unsigned long kMaxContinuousVibrationOnMs = 250UL;

float clampFloat(const float value, const float minimum, const float maximum) {
  if (value < minimum) {
    return minimum;
  }

  if (value > maximum) {
    return maximum;
  }

  return value;
}

int16_t roundToInt16(const long value) {
  if (value < static_cast<long>(SHRT_MIN)) {
    return SHRT_MIN;
  }

  if (value > static_cast<long>(SHRT_MAX)) {
    return SHRT_MAX;
  }

  return static_cast<int16_t>(value);
}

int16_t normalizedToPermille(const float value) {
  const float clampedValue =
      clampFloat(value, kMinimumNormalizedInput, kMaximumNormalizedInput);
  const float scaledValue = clampedValue * 1000.0F;
  const long roundedValue = static_cast<long>(
      scaledValue + (scaledValue >= 0.0F ? 0.5F : -0.5F));
  return roundToInt16(roundedValue);
}

int16_t accelerationToMilliG(const float accelInG) {
  const float scaledValue = accelInG * 1000.0F;
  const long roundedValue = static_cast<long>(
      scaledValue + (scaledValue >= 0.0F ? 0.5F : -0.5F));
  return roundToInt16(roundedValue);
}

uint16_t distanceCmToMm(const float distanceCm) {
  float distanceMm = distanceCm * 10.0F;
  if (distanceMm < 0.0F) {
    distanceMm = 0.0F;
  }

  if (distanceMm > 65535.0F) {
    distanceMm = 65535.0F;
  }

  return static_cast<uint16_t>(distanceMm + 0.5F);
}

}  // namespace

FirmwareApplication::FirmwareApplication(const FirmwareApplicationConfig& config,
                                         IByteStream& primaryTransport,
                                         IByteStream* secondaryTransport,
                                         TwoWire& wire)
    : config_(config),
      secondaryTransport_(secondaryTransport),
      primaryProtocolEndpoint_(primaryTransport),
      secondaryProtocolEndpoint_(secondaryTransport == nullptr
                                     ? NullByteStream::instance()
                                     : *secondaryTransport),
      accelerometer_(wire),
      joystick_(config_.joystickConfig),
      distanceSensor_(config_.distanceSensorConfig),
      servoMotor_(config_.servoConfig),
      vibrationMotor_(config_.vibrationMotorConfig),
      sensorSampleInterval_(config_.sensorSampleIntervalMs),
      telemetryInterval_(config_.protocolConfig.telemetryIntervalMs),
      secondaryTelemetryInterval_(
          config_.protocolConfig.secondaryTelemetryIntervalMs),
      accelerometerRetryInterval_(config_.accelerometerRetryIntervalMs),
      transportLock_(config_.protocolConfig.commandTimeoutMs),
      commandSupervisor_(config_.servoConfig.initialAngle,
                         config_.protocolConfig.commandTimeoutMs,
                         kMaxContinuousVibrationOnMs),
      latestSnapshot_() {}

bool FirmwareApplication::begin() {
  const unsigned long nowMs = millis();

  primaryProtocolEndpoint_.begin(config_.protocolConfig.primaryBaudRate);
  if (secondaryTransport_ != nullptr) {
    secondaryProtocolEndpoint_.begin(config_.protocolConfig.secondaryBaudRate);
  }
  transportLock_.clear();
  sensorSampleInterval_.reset(nowMs);
  telemetryInterval_.reset(nowMs);
  secondaryTelemetryInterval_.reset(nowMs);
  accelerometerRetryInterval_.reset(nowMs);

  joystick_.begin();
  const bool distanceSensorInitialized = distanceSensor_.begin();
  const bool servoInitialized = servoMotor_.begin();
  vibrationMotor_.begin();

  setAccelerometerUnavailable();
  static_cast<void>(accelerometer_.begin());
  refreshSensors();
  captureDistanceState();
  applyActuatorCommand(commandSupervisor_.currentCommand(nowMs));

  return distanceSensorInitialized && servoInitialized;
}

void FirmwareApplication::update(const unsigned long nowMs) {
  transportLock_.clearExpired(nowMs);

  primaryProtocolEndpoint_.processIncoming();
  if (secondaryTransport_ != nullptr) {
    secondaryProtocolEndpoint_.processIncoming();
  }

  ActuatorCommand receivedCommand;
  if (primaryProtocolEndpoint_.tryConsumeLatestCommand(receivedCommand) &&
      transportLock_.tryLockTo(TransportChannel::kPrimary, nowMs)) {
    commandSupervisor_.acceptCommand(receivedCommand, nowMs);
  }
  if (secondaryTransport_ != nullptr &&
      secondaryProtocolEndpoint_.tryConsumeLatestCommand(receivedCommand) &&
      transportLock_.tryLockTo(TransportChannel::kSecondary, nowMs)) {
    commandSupervisor_.acceptCommand(receivedCommand, nowMs);
  }

  applyActuatorCommand(commandSupervisor_.currentCommand(nowMs));
  vibrationMotor_.update(nowMs);
  servoMotor_.update(nowMs);

  if (distanceSensor_.update()) {
    captureDistanceState();
  }

  updateAccelerometerInitialization(nowMs);

  if (sensorSampleInterval_.isReady(nowMs)) {
    refreshSensors();
  }

  if (telemetryInterval_.isReady(nowMs)) {
    captureDistanceState();
    static_cast<void>(
        primaryProtocolEndpoint_.sendTelemetrySnapshot(latestSnapshot_));
  }

  if (secondaryTransport_ != nullptr &&
      transportLock_.isActiveTransport(TransportChannel::kSecondary) &&
      secondaryTelemetryInterval_.isReady(nowMs)) {
    captureDistanceState();
    static_cast<void>(
        secondaryProtocolEndpoint_.sendTelemetrySnapshot(latestSnapshot_));
  }
}

bool FirmwareApplication::isSecondaryTransportActive() const {
  return secondaryTransport_ != nullptr &&
         transportLock_.isActiveTransport(TransportChannel::kSecondary);
}

void FirmwareApplication::resetSecondaryTransportReception() {
  if (secondaryTransport_ == nullptr) {
    return;
  }

  secondaryProtocolEndpoint_.resetReception();
}

void FirmwareApplication::applyActuatorCommand(const ActuatorCommand& command) {
  servoMotor_.setTargetAngle(command.servoAngleDegrees);
  vibrationMotor_.setEnabled(command.vibrationEnabled);
}

void FirmwareApplication::captureDistanceState() {
  if (distanceSensor_.lastMeasurementTimedOut()) {
    latestSnapshot_.distanceMm = 0U;
    latestSnapshot_.distanceValid = false;
    latestSnapshot_.distanceTimedOut = true;
    return;
  }

  if (!distanceSensor_.hasReading()) {
    latestSnapshot_.distanceMm = 0U;
    latestSnapshot_.distanceValid = false;
    latestSnapshot_.distanceTimedOut = false;
    return;
  }

  latestSnapshot_.distanceMm = distanceCmToMm(distanceSensor_.distanceCm());
  latestSnapshot_.distanceValid = true;
  latestSnapshot_.distanceTimedOut = false;
}

void FirmwareApplication::refreshSensors() {
  joystick_.refresh();
  const JoystickPosition position = joystick_.getPosition();
  latestSnapshot_.joystickXPermille = normalizedToPermille(position.x);
  latestSnapshot_.joystickYPermille = normalizedToPermille(position.y);
  latestSnapshot_.joystickButtonPressed = joystick_.isButtonPressed();

  if (!accelerometer_.isInitialized()) {
    setAccelerometerUnavailable();
    return;
  }

  if (!accelerometer_.refresh()) {
    setAccelerometerUnavailable();
    return;
  }

  latestSnapshot_.accelXMilliG = accelerationToMilliG(accelerometer_.getAccelX());
  latestSnapshot_.accelYMilliG = accelerationToMilliG(accelerometer_.getAccelY());
  latestSnapshot_.accelZMilliG = accelerationToMilliG(accelerometer_.getAccelZ());
  latestSnapshot_.accelValid = true;
}

void FirmwareApplication::setAccelerometerUnavailable() {
  latestSnapshot_.accelXMilliG = 0;
  latestSnapshot_.accelYMilliG = 0;
  latestSnapshot_.accelZMilliG = 0;
  latestSnapshot_.accelValid = false;
}

void FirmwareApplication::updateAccelerometerInitialization(
    const unsigned long nowMs) {
  if (accelerometer_.isInitialized()) {
    return;
  }

  if (!accelerometerRetryInterval_.isReady(nowMs)) {
    return;
  }

  static_cast<void>(accelerometer_.begin());
  if (!accelerometer_.isInitialized()) {
    setAccelerometerUnavailable();
  }
}
