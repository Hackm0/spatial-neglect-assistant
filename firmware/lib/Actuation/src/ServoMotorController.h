#pragma once

#include <Arduino.h>
#include <Servo.h>

struct ServoMotorConfig {
  uint8_t pin;
  float minAngle;
  float maxAngle;
  float initialAngle;
  float maxDegreesPerSecond;
};

class ServoMotorController {
 public:
  explicit ServoMotorController(const ServoMotorConfig& config);

  bool begin();
  void setTargetAngle(float angle);
  void setImmediateAngle(float angle);
  void update(unsigned long nowMs);

  float currentAngle() const;
  float targetAngle() const;
  bool isAttached() const;

 private:
  static constexpr float kAngleEpsilon = 0.01F;

  float clampAngle(float angle) const;
  float effectiveMinAngle() const;
  float effectiveMaxAngle() const;
  float effectiveMaxDegreesPerSecond() const;
  void writeCurrentAngle();

  const ServoMotorConfig config_;
  Servo servo_;
  float currentAngle_;
  float targetAngle_;
  unsigned long lastUpdateMs_;
  bool hasLastUpdateTime_;
  bool attached_;
};
