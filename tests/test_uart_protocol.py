from __future__ import annotations

from uart_protocol import ActuatorCommand, ProtocolCodec


def test_encode_actuator_command_matches_protocol_example() -> None:
  codec = ProtocolCodec()

  frame = codec.encode_actuator_command_frame(
      ActuatorCommand(servo_angle_degrees=90.0, vibration_enabled=True),
      sequence=0x05,
  )

  assert frame.hex(" ").upper() == "A5 5A 01 01 05 03 84 03 01 6C 16"


def test_feed_bytes_decodes_telemetry_example() -> None:
  codec = ProtocolCodec()
  frame_bytes = bytes.fromhex(
      "A5 5A 01 81 00 0E 41 01 7D 00 06 FF E8 03 F4 01 0C FE 01 05 00 80")

  frames, errors = codec.feed_bytes(frame_bytes)

  assert errors == []
  assert len(frames) == 1
  snapshot = codec.decode_telemetry_payload(frames[0].payload)
  assert snapshot.distance_mm == 321
  assert snapshot.distance_valid is True
  assert snapshot.distance_timed_out is False
  assert snapshot.accel_x_mg == 125
  assert snapshot.accel_y_mg == -250
  assert snapshot.accel_z_mg == 1000
  assert snapshot.accel_valid is True
  assert snapshot.joystick_x_permille == 500
  assert snapshot.joystick_y_permille == -500
  assert snapshot.joystick_button_pressed is True


def test_feed_bytes_reports_bad_crc() -> None:
  codec = ProtocolCodec()
  invalid_frame = bytes.fromhex(
      "A5 5A 01 01 05 03 84 03 01 6C 17")

  frames, errors = codec.feed_bytes(invalid_frame)

  assert frames == []
  assert len(errors) == 1
  assert errors[0].status == "bad CRC"


def test_feed_bytes_reports_bad_version() -> None:
  codec = ProtocolCodec()
  invalid_frame = bytes.fromhex("A5 5A 02 01 05 03 84 03 01 6C 16")

  frames, errors = codec.feed_bytes(invalid_frame)

  assert frames == []
  assert len(errors) == 1
  assert errors[0].status == "bad version"


def test_feed_bytes_rejects_oversized_payload() -> None:
  codec = ProtocolCodec()
  invalid_frame = bytes.fromhex("A5 5A 01 01 05 21")

  frames, errors = codec.feed_bytes(invalid_frame)

  assert frames == []
  assert len(errors) == 1
  assert errors[0].status == "payload length too large"


def test_decode_telemetry_marks_invalid_sensor_values() -> None:
  codec = ProtocolCodec()
  payload = bytes.fromhex("00 00 2A 00 D6 FF E8 03 05 00 FB FF 00 00")

  snapshot = codec.decode_telemetry_payload(payload)

  assert snapshot.distance_mm == 0
  assert snapshot.distance_valid is False
  assert snapshot.distance_timed_out is False
  assert snapshot.accel_valid is False
  assert snapshot.accel_x_mg == 0
  assert snapshot.accel_y_mg == 0
  assert snapshot.accel_z_mg == 0
  assert snapshot.joystick_x_permille == 5
  assert snapshot.joystick_y_permille == -5
  assert snapshot.joystick_button_pressed is False
