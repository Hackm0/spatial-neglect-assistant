#include <unity.h>

#include <vector>

#include "ActuatorCommandSupervisor.h"
#include "IByteStream.h"
#include "ProtocolTypes.h"
#include "UartFrameCodec.h"
#include "UartProtocolEndpoint.h"

namespace {

class FakeByteStream : public IByteStream {
 public:
  void begin(const unsigned long baudRate) override {
    began = true;
    lastBaudRate = baudRate;
  }

  int available() override {
    return static_cast<int>(readBuffer.size() - readIndex);
  }

  int read() override {
    if (readIndex >= readBuffer.size()) {
      return -1;
    }

    return readBuffer[readIndex++];
  }

  size_t write(const uint8_t* data, const size_t length) override {
    for (size_t index = 0U; index < length; ++index) {
      writtenBytes.push_back(data[index]);
    }

    return length;
  }

  void pushBytes(const uint8_t* data, const size_t length) {
    for (size_t index = 0U; index < length; ++index) {
      readBuffer.push_back(data[index]);
    }
  }

  void pushBytes(const std::vector<uint8_t>& data) {
    pushBytes(data.data(), data.size());
  }

  void clearWrittenBytes() {
    writtenBytes.clear();
  }

  bool began = false;
  unsigned long lastBaudRate = 0UL;
  std::vector<uint8_t> readBuffer;
  std::vector<uint8_t> writtenBytes;
  size_t readIndex = 0U;
};

std::vector<uint8_t> makeCommandFrame(const float servoAngleDegrees,
                                      const bool vibrationEnabled) {
  const ActuatorCommand command = {servoAngleDegrees, vibrationEnabled};
  uint8_t payload[UartFrameCodec::kMaxPayloadLength] = {0U};
  uint8_t payloadLength = 0U;
  TEST_ASSERT_TRUE(UartFrameCodec::encodeActuatorCommandPayload(
      command, payload, sizeof(payload), payloadLength));

  UartRawFrame frame;
  frame.type = static_cast<uint8_t>(UartMessageType::kActuatorCommand);
  frame.sequence = 7U;
  frame.payloadLength = payloadLength;
  for (uint8_t index = 0U; index < payloadLength; ++index) {
    frame.payload[index] = payload[index];
  }

  uint8_t encodedFrame[UartFrameCodec::kMaxFrameLength] = {0U};
  size_t encodedLength = 0U;
  TEST_ASSERT_TRUE(UartFrameCodec::encodeFrame(frame, encodedFrame,
                                               sizeof(encodedFrame),
                                               encodedLength));

  return std::vector<uint8_t>(encodedFrame, encodedFrame + encodedLength);
}

void test_encode_and_decode_actuator_command_payload() {
  const ActuatorCommand expectedCommand = {123.4F, true};
  uint8_t payload[UartFrameCodec::kMaxPayloadLength] = {0U};
  uint8_t payloadLength = 0U;

  TEST_ASSERT_TRUE(UartFrameCodec::encodeActuatorCommandPayload(
      expectedCommand, payload, sizeof(payload), payloadLength));
  TEST_ASSERT_EQUAL_UINT8(UartFrameCodec::kActuatorCommandPayloadLength,
                          payloadLength);

  ActuatorCommand decodedCommand;
  TEST_ASSERT_TRUE(UartFrameCodec::decodeActuatorCommandPayload(
      payload, payloadLength, decodedCommand));
  TEST_ASSERT_FLOAT_WITHIN(0.05F, expectedCommand.servoAngleDegrees,
                           decodedCommand.servoAngleDegrees);
  TEST_ASSERT_TRUE(decodedCommand.vibrationEnabled);
}

void test_encode_telemetry_snapshot_payload() {
  const SensorSnapshot snapshot = {321U, true,  false, 125,  -250,
                                   1000, true,  500,   -500, true};
  uint8_t payload[UartFrameCodec::kMaxPayloadLength] = {0U};
  uint8_t payloadLength = 0U;

  TEST_ASSERT_TRUE(UartFrameCodec::encodeTelemetrySnapshotPayload(
      snapshot, payload, sizeof(payload), payloadLength));
  TEST_ASSERT_EQUAL_UINT8(UartFrameCodec::kTelemetrySnapshotPayloadLength,
                          payloadLength);
  TEST_ASSERT_EQUAL_HEX8(0x41, payload[0]);
  TEST_ASSERT_EQUAL_HEX8(0x01, payload[1]);
  TEST_ASSERT_EQUAL_HEX8(0x7D, payload[2]);
  TEST_ASSERT_EQUAL_HEX8(0x00, payload[3]);
  TEST_ASSERT_EQUAL_HEX8(0x01, payload[12]);
  TEST_ASSERT_EQUAL_HEX8(0x05, payload[13]);
}

void test_bad_crc_rejected() {
  FakeByteStream stream;
  UartProtocolEndpoint endpoint(stream);
  endpoint.begin(115200UL);

  std::vector<uint8_t> frame = makeCommandFrame(45.0F, true);
  frame.back() ^= 0xFFU;
  stream.pushBytes(frame);

  endpoint.processIncoming();

  ActuatorCommand command;
  TEST_ASSERT_FALSE(endpoint.tryConsumeLatestCommand(command));
  TEST_ASSERT_EQUAL_UINT32(1U, endpoint.invalidFrameCount());
}

void test_oversized_payload_rejected() {
  FakeByteStream stream;
  UartProtocolEndpoint endpoint(stream);
  endpoint.begin(115200UL);

  const uint8_t invalidHeader[] = {
      UartFrameCodec::kSyncByte1, UartFrameCodec::kSyncByte2,
      UartFrameCodec::kProtocolVersion,
      static_cast<uint8_t>(UartMessageType::kActuatorCommand), 0x00U, 0x21U,
  };
  stream.pushBytes(invalidHeader, sizeof(invalidHeader));

  endpoint.processIncoming();

  TEST_ASSERT_EQUAL_UINT32(1U, endpoint.invalidFrameCount());
}

void test_resync_after_garbage_bytes() {
  FakeByteStream stream;
  UartProtocolEndpoint endpoint(stream);
  endpoint.begin(115200UL);

  const uint8_t garbage[] = {0x00U, 0xFFU, 0x12U, 0x34U, 0xA5U};
  stream.pushBytes(garbage, sizeof(garbage));
  stream.pushBytes(makeCommandFrame(90.0F, false));

  endpoint.processIncoming();

  ActuatorCommand command;
  TEST_ASSERT_TRUE(endpoint.tryConsumeLatestCommand(command));
  TEST_ASSERT_FLOAT_WITHIN(0.05F, 90.0F, command.servoAngleDegrees);
  TEST_ASSERT_FALSE(command.vibrationEnabled);
}

void test_partial_frame_parsing() {
  FakeByteStream stream;
  UartProtocolEndpoint endpoint(stream);
  endpoint.begin(115200UL);

  const std::vector<uint8_t> frame = makeCommandFrame(75.0F, true);
  stream.pushBytes(frame.data(), 4U);
  endpoint.processIncoming();

  ActuatorCommand command;
  TEST_ASSERT_FALSE(endpoint.tryConsumeLatestCommand(command));

  stream.pushBytes(frame.data() + 4U, frame.size() - 4U);
  endpoint.processIncoming();

  TEST_ASSERT_TRUE(endpoint.tryConsumeLatestCommand(command));
  TEST_ASSERT_FLOAT_WITHIN(0.05F, 75.0F, command.servoAngleDegrees);
  TEST_ASSERT_TRUE(command.vibrationEnabled);
}

void test_command_timeout_returns_failsafe_output() {
  ActuatorCommandSupervisor supervisor(90.0F, 250UL);
  TEST_ASSERT_TRUE(supervisor.isFailsafeActive(0UL));

  const ActuatorCommand command = {135.0F, true};
  supervisor.acceptCommand(command, 10UL);

  ActuatorCommand current = supervisor.currentCommand(200UL);
  TEST_ASSERT_FALSE(supervisor.isFailsafeActive(200UL));
  TEST_ASSERT_FLOAT_WITHIN(0.05F, 135.0F, current.servoAngleDegrees);
  TEST_ASSERT_TRUE(current.vibrationEnabled);

  current = supervisor.currentCommand(260UL);
  TEST_ASSERT_TRUE(supervisor.isFailsafeActive(260UL));
  TEST_ASSERT_FLOAT_WITHIN(0.05F, 90.0F, current.servoAngleDegrees);
  TEST_ASSERT_FALSE(current.vibrationEnabled);
}

void test_send_telemetry_snapshot_writes_frame() {
  FakeByteStream stream;
  UartProtocolEndpoint endpoint(stream);
  endpoint.begin(115200UL);

  const SensorSnapshot snapshot = {100U, true, false, 10, 20,
                                   30,   true, 100,  -100, true};
  TEST_ASSERT_TRUE(endpoint.sendTelemetrySnapshot(snapshot));
  TEST_ASSERT_TRUE(stream.began);
  TEST_ASSERT_EQUAL_UINT32(115200UL, stream.lastBaudRate);
  TEST_ASSERT_EQUAL_HEX8(UartFrameCodec::kSyncByte1, stream.writtenBytes[0]);
  TEST_ASSERT_EQUAL_HEX8(UartFrameCodec::kSyncByte2, stream.writtenBytes[1]);
  TEST_ASSERT_EQUAL_HEX8(UartFrameCodec::kProtocolVersion,
                         stream.writtenBytes[2]);
  TEST_ASSERT_EQUAL_HEX8(
      static_cast<uint8_t>(UartMessageType::kTelemetrySnapshot),
      stream.writtenBytes[3]);
  TEST_ASSERT_EQUAL_UINT8(UartFrameCodec::kTelemetrySnapshotPayloadLength,
                          stream.writtenBytes[5]);
}

}  // namespace

extern "C" {
void setUp(void) {}
void tearDown(void) {}
}

int main(int argc, char** argv) {
  (void)argc;
  (void)argv;

  UNITY_BEGIN();
  RUN_TEST(test_encode_and_decode_actuator_command_payload);
  RUN_TEST(test_encode_telemetry_snapshot_payload);
  RUN_TEST(test_bad_crc_rejected);
  RUN_TEST(test_oversized_payload_rejected);
  RUN_TEST(test_resync_after_garbage_bytes);
  RUN_TEST(test_partial_frame_parsing);
  RUN_TEST(test_command_timeout_returns_failsafe_output);
  RUN_TEST(test_send_telemetry_snapshot_writes_frame);
  return UNITY_END();
}
