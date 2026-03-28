#include "UartFrameCodec.h"

namespace {

float clampFloat(const float value, const float minimum, const float maximum) {
  if (value < minimum) {
    return minimum;
  }

  if (value > maximum) {
    return maximum;
  }

  return value;
}

}  // namespace

bool UartFrameCodec::encodeActuatorCommandPayload(
    const ActuatorCommand& command, uint8_t* payloadBuffer,
    const size_t payloadCapacity, uint8_t& payloadLength) {
  if (payloadBuffer == nullptr || payloadCapacity < kActuatorCommandPayloadLength) {
    return false;
  }

  const uint16_t servoAngleTenths =
      clampToUInt16(command.servoAngleDegrees * 10.0F);
  writeUint16LittleEndian(servoAngleTenths, payloadBuffer);
  payloadBuffer[2] = command.vibrationEnabled ? kVibrationEnabledFlagMask : 0U;
  payloadLength = kActuatorCommandPayloadLength;
  return true;
}

bool UartFrameCodec::decodeActuatorCommandPayload(const uint8_t* payloadBuffer,
                                                  const uint8_t payloadLength,
                                                  ActuatorCommand& command) {
  if (payloadBuffer == nullptr || payloadLength != kActuatorCommandPayloadLength) {
    return false;
  }

  const uint8_t flags = payloadBuffer[2];
  if ((flags & static_cast<uint8_t>(~kVibrationEnabledFlagMask)) != 0U) {
    return false;
  }

  command.servoAngleDegrees =
      static_cast<float>(readUint16LittleEndian(payloadBuffer)) / 10.0F;
  command.vibrationEnabled = (flags & kVibrationEnabledFlagMask) != 0U;
  return true;
}

bool UartFrameCodec::encodeTelemetrySnapshotPayload(
    const SensorSnapshot& snapshot, uint8_t* payloadBuffer,
    const size_t payloadCapacity, uint8_t& payloadLength) {
  if (payloadBuffer == nullptr ||
      payloadCapacity < kTelemetrySnapshotPayloadLength) {
    return false;
  }

  writeUint16LittleEndian(snapshot.distanceMm, payloadBuffer);
  writeInt16LittleEndian(snapshot.accelXMilliG, payloadBuffer + 2U);
  writeInt16LittleEndian(snapshot.accelYMilliG, payloadBuffer + 4U);
  writeInt16LittleEndian(snapshot.accelZMilliG, payloadBuffer + 6U);
  writeInt16LittleEndian(snapshot.joystickXPermille, payloadBuffer + 8U);
  writeInt16LittleEndian(snapshot.joystickYPermille, payloadBuffer + 10U);
  payloadBuffer[12] =
      snapshot.joystickButtonPressed ? kJoystickButtonPressedFlagMask : 0U;

  uint8_t sensorFlags = 0U;
  if (snapshot.distanceValid) {
    sensorFlags |= kDistanceValidFlagMask;
  }
  if (snapshot.distanceTimedOut) {
    sensorFlags |= kDistanceTimedOutFlagMask;
  }
  if (snapshot.accelValid) {
    sensorFlags |= kAccelerometerValidFlagMask;
  }

  payloadBuffer[13] = sensorFlags;
  payloadLength = kTelemetrySnapshotPayloadLength;
  return true;
}

bool UartFrameCodec::encodeFrame(const UartRawFrame& frame,
                                 uint8_t* frameBuffer,
                                 const size_t frameCapacity,
                                 size_t& frameLength) {
  if (frameBuffer == nullptr || frame.payloadLength > kMaxPayloadLength) {
    return false;
  }

  const size_t requiredLength = 2U + 4U + frame.payloadLength + 2U;
  if (frameCapacity < requiredLength) {
    return false;
  }

  frameBuffer[0] = kSyncByte1;
  frameBuffer[1] = kSyncByte2;
  frameBuffer[2] = kProtocolVersion;
  frameBuffer[3] = frame.type;
  frameBuffer[4] = frame.sequence;
  frameBuffer[5] = frame.payloadLength;

  for (uint8_t index = 0U; index < frame.payloadLength; ++index) {
    frameBuffer[6U + index] = frame.payload[index];
  }

  const uint16_t crc =
      calculateCrc(frameBuffer + 2U, 4U + static_cast<size_t>(frame.payloadLength));
  frameBuffer[6U + frame.payloadLength] = static_cast<uint8_t>(crc & 0x00FFU);
  frameBuffer[7U + frame.payloadLength] = static_cast<uint8_t>(crc >> 8U);
  frameLength = requiredLength;
  return true;
}

uint16_t UartFrameCodec::calculateCrc(const uint8_t* data, const size_t length) {
  if (data == nullptr) {
    return 0U;
  }

  uint16_t crc = 0xFFFFU;

  for (size_t index = 0U; index < length; ++index) {
    crc ^= static_cast<uint16_t>(data[index]) << 8U;

    for (uint8_t bit = 0U; bit < 8U; ++bit) {
      if ((crc & 0x8000U) != 0U) {
        crc = static_cast<uint16_t>((crc << 1U) ^ 0x1021U);
      } else {
        crc = static_cast<uint16_t>(crc << 1U);
      }
    }
  }

  return crc;
}

uint16_t UartFrameCodec::clampToUInt16(float value) {
  value = clampFloat(value, 0.0F, 65535.0F);
  return static_cast<uint16_t>(value + 0.5F);
}

void UartFrameCodec::writeUint16LittleEndian(const uint16_t value,
                                             uint8_t* buffer) {
  buffer[0] = static_cast<uint8_t>(value & 0x00FFU);
  buffer[1] = static_cast<uint8_t>(value >> 8U);
}

void UartFrameCodec::writeInt16LittleEndian(const int16_t value,
                                            uint8_t* buffer) {
  writeUint16LittleEndian(static_cast<uint16_t>(value), buffer);
}

uint16_t UartFrameCodec::readUint16LittleEndian(const uint8_t* buffer) {
  return static_cast<uint16_t>(buffer[0]) |
         static_cast<uint16_t>(buffer[1]) << 8U;
}
