from __future__ import annotations

import threading
import time

from mobile_ingestion.arduino import PySerialArduinoController
from uart_protocol import ActuatorCommand, ProtocolCodec


class FakeSerialPort:

  def __init__(self) -> None:
    self._incoming = bytearray()
    self._lock = threading.Lock()
    self.writes: list[bytes] = []
    self.closed = False

  @property
  def in_waiting(self) -> int:
    with self._lock:
      return len(self._incoming)

  def queue_bytes(self, data: bytes) -> None:
    with self._lock:
      self._incoming.extend(data)

  def read(self, size: int) -> bytes:
    with self._lock:
      if not self._incoming:
        pass
      else:
        read_size = min(size, len(self._incoming))
        data = bytes(self._incoming[:read_size])
        del self._incoming[:read_size]
        return data
    time.sleep(0.005)
    return b""

  def write(self, data: bytes) -> int:
    with self._lock:
      self.writes.append(bytes(data))
    return len(data)

  def close(self) -> None:
    self.closed = True

  def reset_input_buffer(self) -> None:
    with self._lock:
      self._incoming.clear()

  def reset_output_buffer(self) -> None:
    return None


def wait_until(predicate, timeout: float = 1.5) -> None:
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.01)
  raise AssertionError("Timed out waiting for condition.")


def build_controller(fake_port: FakeSerialPort,
                     log_capacity: int = 500) -> PySerialArduinoController:
  return PySerialArduinoController(
      keepalive_ms=10,
      reset_settle_seconds=0.0,
      log_capacity=log_capacity,
      shutdown_timeout_seconds=1.0,
      serial_factory=lambda **_: fake_port,
      ports_lister=lambda: ("/dev/ttyUSB0",),
  )


def decode_last_command(frame_bytes: bytes) -> tuple[float, bool]:
  codec = ProtocolCodec()
  frames, errors = codec.feed_bytes(frame_bytes)
  assert errors == []
  payload = frames[0].payload
  servo_angle = int.from_bytes(payload[0:2], byteorder="little") / 10.0
  vibration_enabled = (payload[2] & ProtocolCodec.VIBRATION_ENABLED_FLAG_MASK) != 0
  return servo_angle, vibration_enabled


def build_telemetry_frame(sequence: int = 0) -> bytes:
  codec = ProtocolCodec()
  payload = bytes.fromhex("41 01 7D 00 06 FF E8 03 F4 01 0C FE 01 05")
  header = bytes((
      ProtocolCodec.PROTOCOL_VERSION,
      ProtocolCodec.MESSAGE_TYPE_TELEMETRY_SNAPSHOT,
      sequence,
      len(payload),
  ))
  crc = codec.calculate_crc(header + payload)
  return bytes((ProtocolCodec.SYNC_BYTE_1, ProtocolCodec.SYNC_BYTE_2)
               ) + header + payload + crc.to_bytes(2, byteorder="little")


def test_controller_connects_and_disconnects_with_safe_command() -> None:
  fake_port = FakeSerialPort()
  controller = build_controller(fake_port)

  controller.connect("/dev/ttyUSB0")
  wait_until(lambda: controller.get_snapshot().connected)
  assert controller.get_snapshot().selected_port == "/dev/ttyUSB0"
  assert fake_port.writes

  controller.disconnect()
  wait_until(lambda: fake_port.closed)

  snapshot = controller.get_snapshot()
  assert snapshot.connected is False
  assert snapshot.keepalive_active is False
  assert decode_last_command(fake_port.writes[-1]) == (90.0, False)


def test_controller_tracks_telemetry_invalid_frames_and_log_capacity() -> None:
  fake_port = FakeSerialPort()
  controller = build_controller(fake_port, log_capacity=2)

  controller.connect("/dev/ttyUSB0")
  wait_until(lambda: controller.get_snapshot().connected)

  fake_port.queue_bytes(build_telemetry_frame(sequence=1))
  fake_port.queue_bytes(bytes.fromhex("A5 5A 01 01 01 03 84 03 01 6C 17"))

  wait_until(lambda: controller.get_snapshot().invalid_frame_count >= 1
             and controller.get_snapshot().latest_telemetry is not None)
  snapshot = controller.get_snapshot()

  assert snapshot.latest_telemetry is not None
  assert snapshot.latest_telemetry.distance_mm == 321
  assert snapshot.rx_count >= 1
  assert snapshot.invalid_frame_count >= 1
  assert len(snapshot.recent_frames) <= 2

  controller.disconnect()


def test_debug_override_precedence_reverts_to_backend_command() -> None:
  fake_port = FakeSerialPort()
  controller = build_controller(fake_port)

  controller.connect("/dev/ttyUSB0")
  wait_until(lambda: controller.get_snapshot().connected)

  controller.set_backend_command(ActuatorCommand(120.0, False))
  wait_until(lambda: decode_last_command(fake_port.writes[-1]) == (120.0, False))
  assert controller.get_snapshot().effective_command == ActuatorCommand(120.0,
                                                                        False)

  controller.set_debug_enabled(True)
  controller.set_debug_command(ActuatorCommand(30.0, True))
  wait_until(lambda: decode_last_command(fake_port.writes[-1]) == (30.0, True))
  assert controller.get_snapshot().effective_command == ActuatorCommand(30.0,
                                                                        True)

  controller.set_debug_enabled(False)
  wait_until(lambda: decode_last_command(fake_port.writes[-1]) == (120.0, False))
  assert controller.get_snapshot().effective_command == ActuatorCommand(120.0,
                                                                        False)

  controller.disconnect()
