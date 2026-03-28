#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ProtocolTypes.h"

struct UartRawFrame {
  uint8_t type = 0U;
  uint8_t sequence = 0U;
  uint8_t payloadLength = 0U;
  uint8_t payload[32] = {0U};
};

class UartFrameCodec {
 public:
  static constexpr uint8_t kSyncByte1 = 0xA5U;
  static constexpr uint8_t kSyncByte2 = 0x5AU;
  static constexpr uint8_t kProtocolVersion = 0x01U;
  static constexpr uint8_t kMaxPayloadLength = 32U;
  static constexpr size_t kMaxFrameLength = 2U + 4U + kMaxPayloadLength + 2U;

  static constexpr uint8_t kActuatorCommandPayloadLength = 3U;
  static constexpr uint8_t kTelemetrySnapshotPayloadLength = 14U;

  static constexpr uint8_t kVibrationEnabledFlagMask = 0x01U;
  static constexpr uint8_t kJoystickButtonPressedFlagMask = 0x01U;
  static constexpr uint8_t kDistanceValidFlagMask = 0x01U;
  static constexpr uint8_t kDistanceTimedOutFlagMask = 0x02U;
  static constexpr uint8_t kAccelerometerValidFlagMask = 0x04U;

  static bool encodeActuatorCommandPayload(const ActuatorCommand& command,
                                           uint8_t* payloadBuffer,
                                           size_t payloadCapacity,
                                           uint8_t& payloadLength);
  static bool decodeActuatorCommandPayload(const uint8_t* payloadBuffer,
                                           uint8_t payloadLength,
                                           ActuatorCommand& command);
  static bool encodeTelemetrySnapshotPayload(const SensorSnapshot& snapshot,
                                             uint8_t* payloadBuffer,
                                             size_t payloadCapacity,
                                             uint8_t& payloadLength);
  static bool encodeFrame(const UartRawFrame& frame,
                          uint8_t* frameBuffer,
                          size_t frameCapacity,
                          size_t& frameLength);
  static uint16_t calculateCrc(const uint8_t* data, size_t length);

 private:
  static uint16_t clampToUInt16(float value);
  static void writeUint16LittleEndian(uint16_t value, uint8_t* buffer);
  static void writeInt16LittleEndian(int16_t value, uint8_t* buffer);
  static uint16_t readUint16LittleEndian(const uint8_t* buffer);
};
