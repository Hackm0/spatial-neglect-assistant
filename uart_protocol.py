from __future__ import annotations

import time
from dataclasses import dataclass


DEFAULT_BAUD_RATE = 9600
DEFAULT_KEEPALIVE_MS = 200
ARDUINO_RESET_SETTLE_SECONDS = 2.0
NEUTRAL_SERVO_ANGLE = 90.0
SERVO_MIN_ANGLE = 0.0
SERVO_MAX_ANGLE = 180.0


@dataclass(frozen=True, slots=True)
class ActuatorCommand:
  servo_angle_degrees: float
  vibration_enabled: bool


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
  distance_mm: int
  distance_valid: bool
  distance_timed_out: bool
  accel_x_mg: int
  accel_y_mg: int
  accel_z_mg: int
  accel_valid: bool
  joystick_x_permille: int
  joystick_y_permille: int
  joystick_button_pressed: bool


@dataclass(frozen=True, slots=True)
class RawFrameEvent:
  direction: str
  message_type: int | None
  sequence: int | None
  hex_string: str
  timestamp: float
  status: str


@dataclass(frozen=True, slots=True)
class ParsedFrame:
  message_type: int
  sequence: int
  payload: bytes
  frame_bytes: bytes


class ProtocolCodec:
  SYNC_BYTE_1 = 0xA5
  SYNC_BYTE_2 = 0x5A
  PROTOCOL_VERSION = 0x01
  MESSAGE_TYPE_ACTUATOR_COMMAND = 0x01
  MESSAGE_TYPE_TELEMETRY_SNAPSHOT = 0x81

  ACTUATOR_COMMAND_PAYLOAD_LENGTH = 3
  TELEMETRY_SNAPSHOT_PAYLOAD_LENGTH = 14
  MAX_PAYLOAD_LENGTH = 32

  VIBRATION_ENABLED_FLAG_MASK = 0x01
  JOYSTICK_BUTTON_PRESSED_FLAG_MASK = 0x01
  DISTANCE_VALID_FLAG_MASK = 0x01
  DISTANCE_TIMED_OUT_FLAG_MASK = 0x02
  ACCELEROMETER_VALID_FLAG_MASK = 0x04

  _STATE_WAIT_SYNC_1 = 0
  _STATE_WAIT_SYNC_2 = 1
  _STATE_READ_VERSION = 2
  _STATE_READ_TYPE = 3
  _STATE_READ_SEQUENCE = 4
  _STATE_READ_LENGTH = 5
  _STATE_READ_PAYLOAD = 6
  _STATE_READ_CRC_LOW = 7
  _STATE_READ_CRC_HIGH = 8

  def __init__(self) -> None:
    self.reset_parser()

  @staticmethod
  def clamp_servo_angle(angle_degrees: float) -> float:
    if angle_degrees < SERVO_MIN_ANGLE:
      return SERVO_MIN_ANGLE
    if angle_degrees > SERVO_MAX_ANGLE:
      return SERVO_MAX_ANGLE
    return angle_degrees

  @staticmethod
  def clamp_command(command: ActuatorCommand) -> ActuatorCommand:
    return ActuatorCommand(
        servo_angle_degrees=ProtocolCodec.clamp_servo_angle(
            command.servo_angle_degrees),
        vibration_enabled=command.vibration_enabled,
    )

  @staticmethod
  def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)

  @staticmethod
  def format_message_type(message_type: int | None) -> str:
    if message_type is None:
      return "--"
    if message_type == ProtocolCodec.MESSAGE_TYPE_ACTUATOR_COMMAND:
      return "0x01"
    if message_type == ProtocolCodec.MESSAGE_TYPE_TELEMETRY_SNAPSHOT:
      return "0x81"
    return f"0x{message_type:02X}"

  @staticmethod
  def calculate_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
      crc ^= byte << 8
      for _ in range(8):
        if (crc & 0x8000) != 0:
          crc = ((crc << 1) ^ 0x1021) & 0xFFFF
        else:
          crc = (crc << 1) & 0xFFFF
    return crc

  def encode_actuator_command_frame(self, command: ActuatorCommand,
                                    sequence: int) -> bytes:
    clamped_command = self.clamp_command(command)
    servo_tenths = int((clamped_command.servo_angle_degrees * 10.0) + 0.5)
    payload = bytearray(self.ACTUATOR_COMMAND_PAYLOAD_LENGTH)
    payload[0:2] = servo_tenths.to_bytes(2, byteorder="little", signed=False)
    payload[2] = (self.VIBRATION_ENABLED_FLAG_MASK
                  if clamped_command.vibration_enabled else 0)
    return self._encode_frame(self.MESSAGE_TYPE_ACTUATOR_COMMAND, sequence,
                              bytes(payload))

  def decode_telemetry_payload(self, payload: bytes) -> TelemetrySnapshot:
    if len(payload) != self.TELEMETRY_SNAPSHOT_PAYLOAD_LENGTH:
      raise ValueError(
          f"expected {self.TELEMETRY_SNAPSHOT_PAYLOAD_LENGTH} payload bytes")

    distance_mm = int.from_bytes(payload[0:2], byteorder="little", signed=False)
    accel_x_mg = int.from_bytes(payload[2:4], byteorder="little", signed=True)
    accel_y_mg = int.from_bytes(payload[4:6], byteorder="little", signed=True)
    accel_z_mg = int.from_bytes(payload[6:8], byteorder="little", signed=True)
    joystick_x_permille = int.from_bytes(payload[8:10], byteorder="little",
                                         signed=True)
    joystick_y_permille = int.from_bytes(payload[10:12], byteorder="little",
                                         signed=True)
    button_flags = payload[12]
    sensor_flags = payload[13]

    distance_valid = (sensor_flags & self.DISTANCE_VALID_FLAG_MASK) != 0
    distance_timed_out = (sensor_flags & self.DISTANCE_TIMED_OUT_FLAG_MASK) != 0
    accel_valid = (sensor_flags & self.ACCELEROMETER_VALID_FLAG_MASK) != 0

    return TelemetrySnapshot(
        distance_mm=distance_mm if distance_valid else 0,
        distance_valid=distance_valid,
        distance_timed_out=distance_timed_out,
        accel_x_mg=accel_x_mg if accel_valid else 0,
        accel_y_mg=accel_y_mg if accel_valid else 0,
        accel_z_mg=accel_z_mg if accel_valid else 0,
        accel_valid=accel_valid,
        joystick_x_permille=joystick_x_permille,
        joystick_y_permille=joystick_y_permille,
        joystick_button_pressed=(button_flags &
                                 self.JOYSTICK_BUTTON_PRESSED_FLAG_MASK) != 0,
    )

  def feed_bytes(self, incoming_bytes: bytes
                 ) -> tuple[list[ParsedFrame], list[RawFrameEvent]]:
    frames: list[ParsedFrame] = []
    errors: list[RawFrameEvent] = []

    for byte in incoming_bytes:
      if self._parser_state == self._STATE_WAIT_SYNC_1:
        if byte == self.SYNC_BYTE_1:
          self._frame_prefix = bytearray((self.SYNC_BYTE_1,))
          self._parser_state = self._STATE_WAIT_SYNC_2
        continue

      if self._parser_state == self._STATE_WAIT_SYNC_2:
        if byte == self.SYNC_BYTE_2:
          self._frame_prefix.append(byte)
          self._parser_state = self._STATE_READ_VERSION
        elif byte == self.SYNC_BYTE_1:
          self._frame_prefix = bytearray((self.SYNC_BYTE_1,))
        else:
          self.reset_parser()
        continue

      self._frame_prefix.append(byte)

      if self._parser_state == self._STATE_READ_VERSION:
        if byte != self.PROTOCOL_VERSION:
          errors.append(self._make_error_event("bad version"))
          self.reset_parser()
          continue

        self._frame_data = bytearray((byte,))
        self._parser_state = self._STATE_READ_TYPE
        continue

      if self._parser_state == self._STATE_READ_TYPE:
        self._parsed_message_type = byte
        self._frame_data.append(byte)
        self._parser_state = self._STATE_READ_SEQUENCE
        continue

      if self._parser_state == self._STATE_READ_SEQUENCE:
        self._parsed_sequence = byte
        self._frame_data.append(byte)
        self._parser_state = self._STATE_READ_LENGTH
        continue

      if self._parser_state == self._STATE_READ_LENGTH:
        if byte > self.MAX_PAYLOAD_LENGTH:
          errors.append(self._make_error_event("payload length too large"))
          self.reset_parser()
          continue

        self._parsed_payload_length = byte
        self._frame_data.append(byte)
        self._payload_buffer = bytearray()
        if byte == 0:
          self._parser_state = self._STATE_READ_CRC_LOW
        else:
          self._parser_state = self._STATE_READ_PAYLOAD
        continue

      if self._parser_state == self._STATE_READ_PAYLOAD:
        self._payload_buffer.append(byte)
        self._frame_data.append(byte)
        if len(self._payload_buffer) >= self._parsed_payload_length:
          self._parser_state = self._STATE_READ_CRC_LOW
        continue

      if self._parser_state == self._STATE_READ_CRC_LOW:
        self._received_crc = byte
        self._parser_state = self._STATE_READ_CRC_HIGH
        continue

      if self._parser_state == self._STATE_READ_CRC_HIGH:
        self._received_crc |= byte << 8
        expected_crc = self.calculate_crc(bytes(self._frame_data))
        if self._received_crc != expected_crc:
          errors.append(self._make_error_event("bad CRC"))
          self.reset_parser()
          continue

        frames.append(
            ParsedFrame(
                message_type=self._parsed_message_type,
                sequence=self._parsed_sequence,
                payload=bytes(self._payload_buffer),
                frame_bytes=bytes(self._frame_prefix),
            ))
        self.reset_parser()

    return frames, errors

  def reset_parser(self) -> None:
    self._parser_state = self._STATE_WAIT_SYNC_1
    self._parsed_message_type = 0
    self._parsed_sequence = 0
    self._parsed_payload_length = 0
    self._payload_buffer = bytearray()
    self._frame_data = bytearray()
    self._frame_prefix = bytearray()
    self._received_crc = 0

  def _encode_frame(self, message_type: int, sequence: int,
                    payload: bytes) -> bytes:
    if len(payload) > self.MAX_PAYLOAD_LENGTH:
      raise ValueError("payload too large")

    header = bytes((
        self.PROTOCOL_VERSION,
        message_type & 0xFF,
        sequence & 0xFF,
        len(payload),
    ))
    crc = self.calculate_crc(header + payload)
    return bytes((self.SYNC_BYTE_1, self.SYNC_BYTE_2)) + header + payload + (
        crc.to_bytes(2, byteorder="little", signed=False))

  def _make_error_event(self, status: str) -> RawFrameEvent:
    return RawFrameEvent(
        direction="rx",
        message_type=None,
        sequence=None,
        hex_string=self.bytes_to_hex(bytes(self._frame_prefix)),
        timestamp=time.time(),
        status=status,
    )


__all__ = [
    "ARDUINO_RESET_SETTLE_SECONDS",
    "ActuatorCommand",
    "DEFAULT_BAUD_RATE",
    "DEFAULT_KEEPALIVE_MS",
    "NEUTRAL_SERVO_ANGLE",
    "ParsedFrame",
    "ProtocolCodec",
    "RawFrameEvent",
    "SERVO_MAX_ANGLE",
    "SERVO_MIN_ANGLE",
    "TelemetrySnapshot",
]
