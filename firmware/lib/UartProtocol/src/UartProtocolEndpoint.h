#pragma once

#include <stdint.h>

#include "IByteStream.h"
#include "ProtocolTypes.h"
#include "UartFrameCodec.h"

class UartProtocolEndpoint {
 public:
  explicit UartProtocolEndpoint(IByteStream& stream);

  void begin(unsigned long baudRate);
  void processIncoming();
  bool tryConsumeLatestCommand(ActuatorCommand& command);
  bool sendTelemetrySnapshot(const SensorSnapshot& snapshot);

  uint32_t invalidFrameCount() const;

 private:
  enum class ParseState : uint8_t {
    kWaitingForSyncByte1,
    kWaitingForSyncByte2,
    kReadingVersion,
    kReadingType,
    kReadingSequence,
    kReadingPayloadLength,
    kReadingPayload,
    kReadingCrcLow,
    kReadingCrcHigh,
  };

  void resetParser();
  void rejectFrame();
  void consumeByte(uint8_t byte);
  void handleCompletedFrame();

  IByteStream& stream_;
  ParseState parseState_;
  UartRawFrame parsedFrame_;
  uint8_t payloadIndex_;
  uint16_t receivedCrc_;
  uint8_t frameDataBuffer_[4U + UartFrameCodec::kMaxPayloadLength];
  uint8_t transmitBuffer_[UartFrameCodec::kMaxFrameLength];
  ActuatorCommand latestCommand_;
  bool hasPendingCommand_;
  uint8_t nextSequence_;
  uint32_t invalidFrameCount_;
};
