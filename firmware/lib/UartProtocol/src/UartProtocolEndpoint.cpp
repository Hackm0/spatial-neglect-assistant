#include "UartProtocolEndpoint.h"

UartProtocolEndpoint::UartProtocolEndpoint(IByteStream& stream)
    : stream_(stream),
      parseState_(ParseState::kWaitingForSyncByte1),
      parsedFrame_(),
      payloadIndex_(0U),
      receivedCrc_(0U),
      frameDataBuffer_{0U},
      transmitBuffer_{0U},
      latestCommand_(),
      hasPendingCommand_(false),
      nextSequence_(0U),
      invalidFrameCount_(0U) {}

void UartProtocolEndpoint::begin(const unsigned long baudRate) {
  stream_.begin(baudRate);
  resetParser();
  hasPendingCommand_ = false;
  nextSequence_ = 0U;
  invalidFrameCount_ = 0U;
}

void UartProtocolEndpoint::processIncoming() {
  while (stream_.available() > 0) {
    const int incomingByte = stream_.read();
    if (incomingByte < 0) {
      return;
    }

    consumeByte(static_cast<uint8_t>(incomingByte));
  }
}

bool UartProtocolEndpoint::tryConsumeLatestCommand(ActuatorCommand& command) {
  if (!hasPendingCommand_) {
    return false;
  }

  command = latestCommand_;
  hasPendingCommand_ = false;
  return true;
}

bool UartProtocolEndpoint::sendTelemetrySnapshot(const SensorSnapshot& snapshot) {
  UartRawFrame transmitFrame;
  uint8_t payloadLength = 0U;
  if (!UartFrameCodec::encodeTelemetrySnapshotPayload(
          snapshot, transmitFrame.payload, sizeof(transmitFrame.payload),
          payloadLength)) {
    return false;
  }

  transmitFrame.type =
      static_cast<uint8_t>(UartMessageType::kTelemetrySnapshot);
  transmitFrame.sequence = nextSequence_;
  transmitFrame.payloadLength = payloadLength;

  size_t frameLength = 0U;
  if (!UartFrameCodec::encodeFrame(transmitFrame, transmitBuffer_,
                                   sizeof(transmitBuffer_), frameLength)) {
    return false;
  }

  if (stream_.write(transmitBuffer_, frameLength) != frameLength) {
    return false;
  }

  nextSequence_ = static_cast<uint8_t>(nextSequence_ + 1U);
  return true;
}

void UartProtocolEndpoint::resetReception() {
  resetParser();
  hasPendingCommand_ = false;
}

uint32_t UartProtocolEndpoint::invalidFrameCount() const {
  return invalidFrameCount_;
}

void UartProtocolEndpoint::resetParser() {
  parseState_ = ParseState::kWaitingForSyncByte1;
  parsedFrame_.type = 0U;
  parsedFrame_.sequence = 0U;
  parsedFrame_.payloadLength = 0U;
  payloadIndex_ = 0U;
  receivedCrc_ = 0U;
}

void UartProtocolEndpoint::rejectFrame() {
  ++invalidFrameCount_;
  resetParser();
}

void UartProtocolEndpoint::consumeByte(const uint8_t byte) {
  switch (parseState_) {
    case ParseState::kWaitingForSyncByte1:
      if (byte == UartFrameCodec::kSyncByte1) {
        parseState_ = ParseState::kWaitingForSyncByte2;
      }
      return;

    case ParseState::kWaitingForSyncByte2:
      if (byte == UartFrameCodec::kSyncByte2) {
        parseState_ = ParseState::kReadingVersion;
      } else if (byte != UartFrameCodec::kSyncByte1) {
        parseState_ = ParseState::kWaitingForSyncByte1;
      }
      return;

    case ParseState::kReadingVersion:
      if (byte != UartFrameCodec::kProtocolVersion) {
        rejectFrame();
        return;
      }

      frameDataBuffer_[0] = byte;
      parseState_ = ParseState::kReadingType;
      return;

    case ParseState::kReadingType:
      parsedFrame_.type = byte;
      frameDataBuffer_[1] = byte;
      parseState_ = ParseState::kReadingSequence;
      return;

    case ParseState::kReadingSequence:
      parsedFrame_.sequence = byte;
      frameDataBuffer_[2] = byte;
      parseState_ = ParseState::kReadingPayloadLength;
      return;

    case ParseState::kReadingPayloadLength:
      if (byte > UartFrameCodec::kMaxPayloadLength) {
        rejectFrame();
        return;
      }

      parsedFrame_.payloadLength = byte;
      frameDataBuffer_[3] = byte;
      payloadIndex_ = 0U;
      parseState_ = byte == 0U ? ParseState::kReadingCrcLow
                               : ParseState::kReadingPayload;
      return;

    case ParseState::kReadingPayload:
      parsedFrame_.payload[payloadIndex_] = byte;
      frameDataBuffer_[4U + payloadIndex_] = byte;
      ++payloadIndex_;
      if (payloadIndex_ >= parsedFrame_.payloadLength) {
        parseState_ = ParseState::kReadingCrcLow;
      }
      return;

    case ParseState::kReadingCrcLow:
      receivedCrc_ = byte;
      parseState_ = ParseState::kReadingCrcHigh;
      return;

    case ParseState::kReadingCrcHigh:
      receivedCrc_ |= static_cast<uint16_t>(byte) << 8U;
      handleCompletedFrame();
      return;
  }
}

void UartProtocolEndpoint::handleCompletedFrame() {
  const uint16_t expectedCrc = UartFrameCodec::calculateCrc(
      frameDataBuffer_, 4U + static_cast<size_t>(parsedFrame_.payloadLength));
  if (receivedCrc_ != expectedCrc) {
    rejectFrame();
    return;
  }

  if (parsedFrame_.type ==
      static_cast<uint8_t>(UartMessageType::kActuatorCommand)) {
    ActuatorCommand decodedCommand;
    if (!UartFrameCodec::decodeActuatorCommandPayload(
            parsedFrame_.payload, parsedFrame_.payloadLength, decodedCommand)) {
      rejectFrame();
      return;
    }

    latestCommand_ = decodedCommand;
    hasPendingCommand_ = true;
  }

  resetParser();
}
