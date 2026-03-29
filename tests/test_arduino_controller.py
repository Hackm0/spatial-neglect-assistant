from __future__ import annotations

import threading
import time

import pytest

import mobile_ingestion.arduino as arduino_module
from arduino_transport import BluetoothConnectionConfig
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
                     *,
                     log_capacity: int = 500,
                     reset_settle_seconds: float = 0.0,
                     serial_factory=None,
                     bluetooth_port_factory=None,
                     ports_lister=None,
                     connection_builder=None,
                     connection_resolver=None,
                     bluetooth_available_probe=None) -> PySerialArduinoController:
  return PySerialArduinoController(
      keepalive_ms=10,
      reset_settle_seconds=reset_settle_seconds,
      log_capacity=log_capacity,
      shutdown_timeout_seconds=1.0,
      serial_factory=serial_factory or (lambda **_: fake_port),
      bluetooth_port_factory=(bluetooth_port_factory
                              or (lambda **_: fake_port)),
      ports_lister=ports_lister or (lambda: ("/dev/ttyUSB0",)),
      connection_builder=connection_builder,
      connection_resolver=connection_resolver,
      bluetooth_available_probe=bluetooth_available_probe,
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
  fake_port.queue_bytes(build_telemetry_frame(sequence=1))
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
  fake_port.queue_bytes(build_telemetry_frame(sequence=1))
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


def test_backend_vibration_remains_active_in_debug_mode() -> None:
  fake_port = FakeSerialPort()
  fake_port.queue_bytes(build_telemetry_frame(sequence=1))
  controller = build_controller(fake_port)

  controller.connect("/dev/ttyUSB0")
  wait_until(lambda: controller.get_snapshot().connected)

  controller.set_debug_enabled(True)
  controller.set_debug_command(ActuatorCommand(30.0, False))
  wait_until(lambda: decode_last_command(fake_port.writes[-1]) == (30.0, False))

  controller.set_backend_command(ActuatorCommand(120.0, True))
  wait_until(lambda: decode_last_command(fake_port.writes[-1]) == (30.0, True))
  assert controller.get_snapshot().effective_command == ActuatorCommand(30.0,
                                                                        True)

  controller.set_backend_command(ActuatorCommand(120.0, False))
  wait_until(lambda: decode_last_command(fake_port.writes[-1]) == (30.0, False))
  assert controller.get_snapshot().effective_command == ActuatorCommand(30.0,
                                                                        False)

  controller.disconnect()


@pytest.mark.parametrize(
    ("port_input", "expected_channel"),
    (
        ("98:d3:11:fd:07:ff", 1),
        ("HC-05 [98:D3:11:FD:07:FF]", 1),
        ("bluetooth://98:D3:11:FD:07:FF/3", 3),
    ),
)
def test_controller_accepts_gui_bluetooth_input_formats(
    port_input: str, expected_channel: int) -> None:
  fake_bluetooth_port = FakeSerialPort()
  fake_bluetooth_port.queue_bytes(build_telemetry_frame(sequence=2))
  serial_calls: list[dict[str, object]] = []
  bluetooth_calls: list[tuple[str, int]] = []

  def serial_factory(**kwargs: object) -> FakeSerialPort:
    serial_calls.append(kwargs)
    return fake_bluetooth_port

  def bluetooth_factory(*, address: str, channel: int, **_: object
                        ) -> FakeSerialPort:
    bluetooth_calls.append((address, channel))
    return fake_bluetooth_port

  controller = build_controller(
      fake_bluetooth_port,
      serial_factory=serial_factory,
      bluetooth_port_factory=bluetooth_factory,
      bluetooth_available_probe=lambda: True,
  )

  controller.connect(port_input)
  wait_until(lambda: controller.get_snapshot().connected)

  snapshot = controller.get_snapshot()
  assert snapshot.selected_port == port_input.strip()
  assert bluetooth_calls == [("98:D3:11:FD:07:FF", expected_channel)]
  assert serial_calls == []

  controller.disconnect()


def test_rfcomm_input_can_resolve_to_direct_bluetooth_transport() -> None:
  fake_bluetooth_port = FakeSerialPort()
  fake_bluetooth_port.queue_bytes(build_telemetry_frame(sequence=3))
  serial_calls: list[dict[str, object]] = []
  bluetooth_calls: list[tuple[str, int]] = []

  def serial_factory(**kwargs: object) -> FakeSerialPort:
    serial_calls.append(kwargs)
    return fake_bluetooth_port

  def bluetooth_factory(*, address: str, channel: int, **_: object
                        ) -> FakeSerialPort:
    bluetooth_calls.append((address, channel))
    return fake_bluetooth_port

  controller = build_controller(
      fake_bluetooth_port,
      serial_factory=serial_factory,
      bluetooth_port_factory=bluetooth_factory,
      connection_resolver=lambda _: BluetoothConnectionConfig(
          address="98:D3:11:FD:07:FF",
          channel=5,
      ),
      bluetooth_available_probe=lambda: True,
  )

  controller.connect("/dev/rfcomm0")
  wait_until(lambda: controller.get_snapshot().connected)

  snapshot = controller.get_snapshot()
  assert snapshot.selected_port == "/dev/rfcomm0"
  assert bluetooth_calls == [("98:D3:11:FD:07:FF", 5)]
  assert serial_calls == []

  controller.disconnect()


def test_rfcomm_input_falls_back_to_serial_when_unresolved(
    monkeypatch: pytest.MonkeyPatch) -> None:
  active_port = FakeSerialPort()
  serial_calls: list[dict[str, object]] = []
  bluetooth_calls: list[tuple[str, int]] = []
  monkeypatch.setattr(arduino_module._SerialWorker, "_lookup_rfcomm_binding",
                      lambda self, port_name: None)

  def serial_factory(**kwargs: object) -> FakeSerialPort:
    serial_calls.append(kwargs)
    return active_port

  def bluetooth_factory(*, address: str, channel: int, **_: object
                        ) -> FakeSerialPort:
    bluetooth_calls.append((address, channel))
    return active_port

  controller = build_controller(
      active_port,
      serial_factory=serial_factory,
      bluetooth_port_factory=bluetooth_factory,
      connection_resolver=lambda connection: connection,
      bluetooth_available_probe=lambda: True,
  )

  controller.connect("/dev/rfcomm0")
  wait_until(lambda: controller.get_snapshot().connected)

  assert serial_calls and serial_calls[0]["port"] == "/dev/rfcomm0"
  assert bluetooth_calls == []

  controller.disconnect()


def test_bluetooth_handshake_timeout_keeps_link_active(
    monkeypatch: pytest.MonkeyPatch) -> None:
  fake_bluetooth_port = FakeSerialPort()
  monkeypatch.setattr(arduino_module, "CONNECTION_HANDSHAKE_TIMEOUT_SECONDS",
                      0.05)
  monkeypatch.setattr(arduino_module, "CONNECTION_HANDSHAKE_RETRY_SECONDS",
                      0.01)

  controller = build_controller(
      fake_bluetooth_port,
      bluetooth_available_probe=lambda: True,
  )

  controller.connect("98:D3:11:FD:07:FF")
  wait_until(lambda: any("telemetry handshake timed out" in event.status
                         for event in controller.get_snapshot().recent_frames),
             timeout=1.0)

  snapshot = controller.get_snapshot()
  assert snapshot.connected is True
  assert snapshot.keepalive_active is True
  assert snapshot.detail in {
      "Bluetooth link active, awaiting telemetry",
      "command sent, awaiting telemetry",
  }

  controller.disconnect()


def test_serial_handshake_timeout_disconnects_worker(
    monkeypatch: pytest.MonkeyPatch) -> None:
  fake_serial_port = FakeSerialPort()
  monkeypatch.setattr(arduino_module, "CONNECTION_HANDSHAKE_TIMEOUT_SECONDS",
                      0.05)
  monkeypatch.setattr(arduino_module, "CONNECTION_HANDSHAKE_RETRY_SECONDS",
                      0.01)
  controller = build_controller(fake_serial_port)

  controller.connect("/dev/ttyUSB0")
  wait_until(lambda: controller.get_snapshot().connected is False
             and any("connection handshake failed" in event.status
                     for event in controller.get_snapshot().recent_frames),
             timeout=1.0)

  snapshot = controller.get_snapshot()
  assert snapshot.detail == "disconnected"
  assert snapshot.connected is False


def test_ports_and_availability_include_bluetooth_transport() -> None:
  controller = build_controller(
      FakeSerialPort(),
      ports_lister=lambda: (
          "/dev/ttyUSB0",
          "/dev/ttyUSB1",
          "HC-05 [98:D3:11:FD:07:FF]",
      ),
      bluetooth_available_probe=lambda: True,
  )

  assert controller.list_ports() == (
      "/dev/ttyUSB0",
      "/dev/ttyUSB1",
      "HC-05 [98:D3:11:FD:07:FF]",
  )
  assert controller.get_snapshot().available is True


def test_availability_stays_true_when_only_bluetooth_is_usable(
    monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(arduino_module, "serial", None)
  monkeypatch.setattr(arduino_module, "SERIAL_IMPORT_ERROR",
                      ImportError("pyserial missing"))

  controller = PySerialArduinoController(
      bluetooth_available_probe=lambda: True,
      ports_lister=lambda: ("HC-05 [98:D3:11:FD:07:FF]",),
  )

  snapshot = controller.get_snapshot()
  assert snapshot.available is True
  assert snapshot.detail == "Bluetooth RFCOMM ready"
  assert controller.list_ports() == ("HC-05 [98:D3:11:FD:07:FF]",)
