#pragma once

#include <Arduino.h>
#include <Wire.h>

#include <ActuatorCommandSupervisor.h>
#include <AutonomousVibrationController.h>
#include <AnalogJoystick.h>
#include <ExclusiveTransportLock.h>
#include <HcSr04DistanceSensor.h>
#include <IByteStream.h>
#include <MillisInterval.h>
#include <Mpu9250Accelerometer.h>
#include <ProtocolTypes.h>
#include <ServoMotorController.h>
#include <UartProtocolEndpoint.h>
#include <VibrationMotorController.h>

struct FirmwareApplicationConfig {
  HcSr04Config distanceSensorConfig;
  AnalogJoystickConfig joystickConfig;
  ServoMotorConfig servoConfig;
  VibrationMotorConfig vibrationMotorConfig;
  ProtocolConfig protocolConfig;
  unsigned long sensorSampleIntervalMs;
  unsigned long accelerometerRetryIntervalMs;
};

class FirmwareApplication {
 public:
  FirmwareApplication(const FirmwareApplicationConfig& config,
                      IByteStream& primaryTransport,
                      IByteStream* secondaryTransport = nullptr,
                      TwoWire& wire = Wire);

  bool begin();
  void update(unsigned long nowMs);
  bool isSecondaryTransportActive() const;
  void resetSecondaryTransportReception();

 private:
  void applyActuatorCommand(const ActuatorCommand& command,
                            bool autonomousVibrationEnabled);
  void captureDistanceState();
  void refreshSensors();
  void setAccelerometerUnavailable();
  void updateAccelerometerInitialization(unsigned long nowMs);

  const FirmwareApplicationConfig config_;
  IByteStream* secondaryTransport_;
  UartProtocolEndpoint primaryProtocolEndpoint_;
  UartProtocolEndpoint secondaryProtocolEndpoint_;
  Mpu9250Accelerometer accelerometer_;
  AnalogJoystick joystick_;
  HcSr04DistanceSensor distanceSensor_;
  ServoMotorController servoMotor_;
  VibrationMotorController vibrationMotor_;
  MillisInterval sensorSampleInterval_;
  MillisInterval telemetryInterval_;
  MillisInterval secondaryTelemetryInterval_;
  MillisInterval accelerometerRetryInterval_;
  ExclusiveTransportLock transportLock_;
  ActuatorCommandSupervisor commandSupervisor_;
  AutonomousVibrationController autonomousVibrationController_;
  SensorSnapshot latestSnapshot_;
};
