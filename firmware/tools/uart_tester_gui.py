#!/usr/bin/env python3
from __future__ import annotations

import argparse
import queue
import re
import select
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Iterable, Optional, Protocol

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from uart_protocol import (ARDUINO_RESET_SETTLE_SECONDS, DEFAULT_BAUD_RATE,
                           DEFAULT_KEEPALIVE_MS, NEUTRAL_SERVO_ANGLE,
                           SERVO_MAX_ANGLE, SERVO_MIN_ANGLE, ActuatorCommand,
                           ProtocolCodec, RawFrameEvent, TelemetrySnapshot)

try:
  import serial
  from serial import SerialException
  from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - exercised only on missing deps
  serial = None
  SerialException = OSError
  list_ports = None
  SERIAL_IMPORT_ERROR = exc
else:
  SERIAL_IMPORT_ERROR = None


DEFAULT_LOG_LINES = 500
COMMON_BAUD_RATES = (
    9600,
    19200,
    38400,
    57600,
    115200,
)
DEFAULT_BLUETOOTH_RFCOMM_CHANNEL = 1
RFCOMM_PORT_PREFIX = "/dev/rfcomm"
RFCOMM_RESET_SETTLE_SECONDS = 0.35
BLUETOOTHCTL_TIMEOUT_SECONDS = 5.0
BLUETOOTH_CONNECTION_TIMEOUT_SECONDS = 4.0
BLUETOOTH_STATE_POLL_INTERVAL_SECONDS = 0.2
BLUETOOTH_SOCKET_CONNECT_TIMEOUT_SECONDS = 6.0
RFCOMM_CONNECTION_TIMEOUT_SECONDS = 2.0
RFCOMM_CONNECTION_POLL_INTERVAL_SECONDS = 0.1
CONNECTION_HANDSHAKE_TIMEOUT_SECONDS = 4.0
CONNECTION_HANDSHAKE_RETRY_SECONDS = 0.1
RFCOMM_LISTING_PATTERN = re.compile(
    r"^(rfcomm\d+):\s+([0-9A-F:]{17})\s+channel\s+(\d+)\s+(.*)$",
    re.IGNORECASE,
)
BLUETOOTH_ADDRESS_PATTERN = re.compile(
    r"([0-9A-F]{2}(?::[0-9A-F]{2}){5})",
    re.IGNORECASE,
)
BLUETOOTH_URI_PATTERN = re.compile(
    r"^(?:bt|bluetooth)://([0-9A-F:]{17})(?:/(\d+))?$",
    re.IGNORECASE,
)
BLUETOOTH_DEVICE_PATTERN = re.compile(
    r"^Device\s+([0-9A-F:]{17})(?:\s+(.*))?$",
    re.IGNORECASE,
)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True, slots=True)
class SerialConnectionConfig:
  port: str

  def describe(self) -> str:
    return self.port


@dataclass(frozen=True, slots=True)
class BluetoothConnectionConfig:
  address: str
  channel: int = DEFAULT_BLUETOOTH_RFCOMM_CHANNEL
  device_name: Optional[str] = None

  def describe(self) -> str:
    if self.device_name:
      return f"{self.device_name} [{self.address}]"
    return f"Bluetooth {self.address}"


@dataclass(frozen=True, slots=True)
class RfcommBinding:
  port: str
  address: str
  channel: int
  state: str


@dataclass(frozen=True, slots=True)
class BluetoothDeviceInfo:
  address: str
  name: str

  def format_option(self) -> str:
    if self.name:
      return f"{self.name} [{self.address}]"
    return f"Bluetooth [{self.address}]"


ConnectionConfig = SerialConnectionConfig | BluetoothConnectionConfig


class ByteStreamPort(Protocol):
  in_waiting: int

  def read(self, size: int) -> bytes:
    raise NotImplementedError

  def write(self, data: bytes) -> int:
    raise NotImplementedError

  def close(self) -> None:
    raise NotImplementedError

  def reset_input_buffer(self) -> None:
    raise NotImplementedError

  def reset_output_buffer(self) -> None:
    raise NotImplementedError


def normalize_port_name(port: str) -> str:
  normalized_port = port.strip()
  if re.fullmatch(r"rfcomm\d+", normalized_port, re.IGNORECASE):
    return f"/dev/{normalized_port.lower()}"
  return normalized_port


def build_connection_config(port: str) -> ConnectionConfig:
  normalized_port = normalize_port_name(port)
  if not normalized_port:
    raise ValueError("Enter a port before connecting.")

  bluetooth_uri_match = BLUETOOTH_URI_PATTERN.match(normalized_port)
  if bluetooth_uri_match is not None:
    channel = DEFAULT_BLUETOOTH_RFCOMM_CHANNEL
    if bluetooth_uri_match.group(2) is not None:
      channel = int(bluetooth_uri_match.group(2), 10)
      if channel <= 0:
        raise ValueError("Bluetooth RFCOMM channel must be greater than zero.")
    return BluetoothConnectionConfig(
        address=bluetooth_uri_match.group(1).upper(),
        channel=channel,
    )

  bluetooth_address_match = BLUETOOTH_ADDRESS_PATTERN.search(normalized_port)
  if bluetooth_address_match is not None:
    label_prefix = normalized_port[:bluetooth_address_match.start()].strip()
    label_prefix = label_prefix.rstrip("[(").strip()
    if label_prefix.lower().startswith("bluetooth "):
      label_prefix = label_prefix[len("bluetooth "):].strip()
    if label_prefix.lower().startswith("bt "):
      label_prefix = label_prefix[len("bt "):].strip()
    return BluetoothConnectionConfig(
        address=bluetooth_address_match.group(1).upper(),
        device_name=label_prefix or None,
    )

  return SerialConnectionConfig(port=normalized_port)


def parse_baud_rate(baud_rate: str) -> int:
  normalized_baud_rate = baud_rate.strip()
  if not normalized_baud_rate:
    raise ValueError("Enter a baud rate before connecting.")

  try:
    parsed_baud_rate = int(normalized_baud_rate, 10)
  except ValueError as exc:
    raise ValueError("Enter a valid integer baud rate.") from exc

  if parsed_baud_rate <= 0:
    raise ValueError("Enter a baud rate greater than zero.")

  return parsed_baud_rate


def is_rfcomm_port(port_name: str) -> bool:
  return port_name.startswith(RFCOMM_PORT_PREFIX)


def connection_reset_settle_seconds(port_name: str) -> float:
  if is_rfcomm_port(port_name):
    return 0.0

  return ARDUINO_RESET_SETTLE_SECONDS


def parse_rfcomm_bindings(rfcomm_output: str) -> dict[str, RfcommBinding]:
  bindings: dict[str, RfcommBinding] = {}
  for line in rfcomm_output.splitlines():
    match = RFCOMM_LISTING_PATTERN.match(line.strip())
    if match is None:
      continue

    device_name, address, channel_text, state = match.groups()
    bindings[f"/dev/{device_name}"] = RfcommBinding(
        port=f"/dev/{device_name}",
        address=address.upper(),
        channel=int(channel_text, 10),
        state=state.strip(),
    )

  return bindings


def strip_ansi_escape_sequences(text: str) -> str:
  return ANSI_ESCAPE_PATTERN.sub("", text)


def parse_bluetooth_devices(
    bluetoothctl_output: str) -> dict[str, BluetoothDeviceInfo]:
  devices: dict[str, BluetoothDeviceInfo] = {}
  sanitized_output = strip_ansi_escape_sequences(bluetoothctl_output)
  for line in sanitized_output.splitlines():
    match = BLUETOOTH_DEVICE_PATTERN.match(line.strip())
    if match is None:
      continue

    address = match.group(1).upper()
    device_name = (match.group(2) or "").strip()
    devices[address] = BluetoothDeviceInfo(address=address, name=device_name)

  return devices


def parse_bluetooth_connected(bluetoothctl_output: str) -> Optional[bool]:
  sanitized_output = strip_ansi_escape_sequences(bluetoothctl_output)
  match = re.search(r"Connected:\s+(yes|no)", sanitized_output, re.IGNORECASE)
  if match is None:
    return None

  return match.group(1).lower() == "yes"


class BluetoothSocketPort:
  _READY_READ_SIZE = 4096

  def __init__(self,
               address: str,
               channel: int,
               timeout_seconds: float = 0.01,
               write_timeout_seconds: float = 0.25) -> None:
    if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(
        socket, "BTPROTO_RFCOMM"):
      raise OSError("Bluetooth RFCOMM sockets are unavailable on this platform.")

    self._read_timeout_seconds = timeout_seconds
    self._write_timeout_seconds = write_timeout_seconds
    self._socket = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                                 socket.BTPROTO_RFCOMM)
    self._socket.settimeout(BLUETOOTH_SOCKET_CONNECT_TIMEOUT_SECONDS)
    self._socket.connect((address, channel))
    self._socket.settimeout(self._read_timeout_seconds)

  @property
  def in_waiting(self) -> int:
    readable, _, _ = select.select([self._socket], [], [], 0.0)
    return self._READY_READ_SIZE if readable else 0

  def read(self, size: int) -> bytes:
    try:
      self._socket.settimeout(self._read_timeout_seconds)
      return self._socket.recv(max(1, size))
    except socket.timeout:
      return b""

  def write(self, data: bytes) -> int:
    self._socket.settimeout(self._write_timeout_seconds)
    self._socket.sendall(data)
    self._socket.settimeout(self._read_timeout_seconds)
    return len(data)

  def reset_input_buffer(self) -> None:
    self._socket.setblocking(False)
    try:
      while True:
        try:
          chunk = self._socket.recv(self._READY_READ_SIZE)
        except BlockingIOError:
          break
        if not chunk:
          break
    finally:
      self._socket.settimeout(self._read_timeout_seconds)

  def reset_output_buffer(self) -> None:
    return

  def close(self) -> None:
    self._socket.close()


def list_bluetooth_device_options() -> list[str]:
  try:
    result = subprocess.run(
        ["bluetoothctl", "devices"],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return []

  combined_output = "\n".join(
      text.strip() for text in (result.stdout, result.stderr) if text.strip())
  devices = parse_bluetooth_devices(combined_output)
  return [
      device.format_option() for device in sorted(
          devices.values(),
          key=lambda device: ((device.name or device.address).lower(),
                              device.address),
      )
  ]


def lookup_rfcomm_binding(port_name: str) -> Optional[RfcommBinding]:
  try:
    result = subprocess.run(
        ["rfcomm"],
        capture_output=True,
        text=True,
        timeout=2.0,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return None

  bindings = parse_rfcomm_bindings(result.stdout)
  return bindings.get(port_name)


def is_bluetooth_connection(connection: ConnectionConfig) -> bool:
  if isinstance(connection, BluetoothConnectionConfig):
    return True
  return is_rfcomm_port(connection.port)


def resolve_connection_config(connection: ConnectionConfig) -> ConnectionConfig:
  if isinstance(connection, BluetoothConnectionConfig):
    return connection

  if not is_rfcomm_port(connection.port):
    return connection

  binding = lookup_rfcomm_binding(connection.port)
  if binding is None:
    return connection

  if not hasattr(socket, "AF_BLUETOOTH") or not hasattr(socket, "BTPROTO_RFCOMM"):
    return connection

  return BluetoothConnectionConfig(address=binding.address, channel=binding.channel)


@dataclass(slots=True)
class WorkerStatus:
  connected: bool
  keepalive_active: bool
  tx_count: int
  rx_count: int
  invalid_frame_count: int
  connection_label: str
  detail: str


class SerialWorker(threading.Thread):
  def __init__(self, connection: ConnectionConfig, baud_rate: int,
               keepalive_ms: int,
               event_queue: "queue.Queue[object]") -> None:
    super().__init__(daemon=True, name="uart-tester-serial-worker")
    self._connection = connection
    self._baud_rate = baud_rate
    self._keepalive_seconds = keepalive_ms / 1000.0
    self._event_queue = event_queue
    self._codec = ProtocolCodec()
    self._current_command = ActuatorCommand(NEUTRAL_SERVO_ANGLE, False)
    self._command_lock = threading.Lock()
    self._command_dirty = threading.Event()
    self._stop_requested = threading.Event()
    self._tx_sequence = 0
    self._tx_count = 0
    self._rx_count = 0
    self._invalid_frame_count = 0
    self._link_active = False
    self._protocol_ready = False
    self._command_dirty.set()

  def update_command(self, command: ActuatorCommand) -> None:
    clamped_command = ActuatorCommand(
        servo_angle_degrees=ProtocolCodec.clamp_servo_angle(
            command.servo_angle_degrees),
        vibration_enabled=command.vibration_enabled,
    )
    with self._command_lock:
      self._current_command = clamped_command
    self._command_dirty.set()

  def stop(self) -> None:
    self._stop_requested.set()

  def run(self) -> None:
    if serial is None and not isinstance(self._connection,
                                         BluetoothConnectionConfig):
      self._publish_log("system", None, None, "",
                        f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
      self._publish_status(False, False, "pyserial unavailable")
      return

    serial_port: Optional[ByteStreamPort] = None
    try:
      self._link_active = False
      self._protocol_ready = False
      self._prepare_connection()
      serial_port = self._open_connection_port(self._connection)
      self._confirm_connection_ready()

      settle_seconds = self._connection_reset_settle_seconds()
      if settle_seconds > 0.0:
        # Opening the Arduino USB serial port resets the board. Give the
        # bootloader time to finish so we start from a clean protocol stream
        # before we begin the keepalive loop.
        time.sleep(settle_seconds)
      self._reset_serial_port(serial_port,
                              allow_failure=is_bluetooth_connection(
                                  self._connection))
      self._link_active = True
      self._command_dirty.set()
      self._publish_log(
          "system",
          None,
          None,
          "",
          self._opened_connection_message(),
      )
      self._publish_status(True, True, "awaiting telemetry handshake")
      handshake_completed = self._perform_connection_handshake(
          serial_port, allow_timeout=is_bluetooth_connection(self._connection))
      if handshake_completed:
        self._publish_status(True, True, "connected")
      elif is_bluetooth_connection(self._connection):
        self._publish_status(True, True, "Bluetooth link active, awaiting telemetry")

      next_keepalive_deadline = time.monotonic()
      while not self._stop_requested.is_set():
        now = time.monotonic()
        if self._command_dirty.is_set() or now >= next_keepalive_deadline:
          self._send_current_command(serial_port)
          next_keepalive_deadline = time.monotonic() + self._keepalive_seconds

        self._process_incoming_bytes(serial_port)
    except (OSError, SerialException) as exc:
      self._publish_log("system", None, None, "", f"serial error: {exc}")
      self._publish_status(False, False, f"serial error: {exc}")
    finally:
      if serial_port is not None:
        try:
          self._write_command(serial_port, ActuatorCommand(NEUTRAL_SERVO_ANGLE,
                                                           False),
                              log_status="disconnect safe command")
        except (OSError, SerialException):
          pass

      self._close_serial_port(serial_port)
      self._cleanup_connection()
      self._link_active = False
      self._protocol_ready = False

      self._publish_log("system", None, None, "", "serial worker stopped")
      self._publish_status(False, False, "disconnected")

  def _prepare_connection(self) -> None:
    if isinstance(self._connection, BluetoothConnectionConfig):
      self._publish_log(
          "system",
          None,
          None,
          "",
          (f"opening direct Bluetooth RFCOMM link to "
           f"{self._connection.address} channel {self._connection.channel}"),
      )
      return

    if not is_rfcomm_port(self._connection.port):
      return

    binding = self._lookup_rfcomm_binding(self._connection.port)
    if binding is None:
      self._publish_log("system", None, None, "",
                        "rfcomm binding not found; continuing with direct port open")
      return

    self._publish_log(
        "system",
        None,
        None,
        "",
        f"using bound Bluetooth port {binding.port} for {binding.address}",
    )
    if "connected" not in binding.state.lower():
      self._request_bluetooth_connect(binding.address)
    self._warm_up_rfcomm_port()

  def _confirm_connection_ready(self) -> None:
    if isinstance(self._connection, BluetoothConnectionConfig):
      connected = self._query_bluetooth_connection_state(self._connection.address)
      if connected:
        self._publish_log(
            "system",
            None,
            None,
            "",
            (f"confirmed direct Bluetooth RFCOMM link active on "
             f"{self._connection.address}"),
        )
      else:
        self._publish_log(
            "system",
            None,
            None,
            "",
            (f"opened direct Bluetooth RFCOMM socket to "
             f"{self._connection.address}"),
        )
      return

    if not is_rfcomm_port(self._connection.port):
      return

    binding = self._lookup_rfcomm_binding(self._connection.port)
    if binding is not None and "connected" in binding.state.lower():
      self._publish_log(
          "system",
          None,
          None,
          "",
          f"confirmed Bluetooth serial link active on {binding.port}: {binding.state}",
      )
      return

    self._publish_log(
        "system",
        None,
        None,
        "",
        (f"RFCOMM state was not confirmed for {self._connection.port}; "
         "continuing with opened serial port"),
    )

  def _cleanup_connection(self) -> None:
    return

  @staticmethod
  def _run_command(arguments: list[str],
                   input_text: Optional[str] = None,
                   timeout_seconds: float = BLUETOOTHCTL_TIMEOUT_SECONDS
                   ) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )

  def _lookup_rfcomm_binding(self, port_name: str) -> Optional[RfcommBinding]:
    try:
      result = self._run_command(["rfcomm"], timeout_seconds=2.0)
    except (OSError, subprocess.SubprocessError) as exc:
      self._publish_log("system", None, None, "",
                        f"rfcomm lookup failed: {exc}")
      return None

    bindings = parse_rfcomm_bindings(result.stdout)
    binding = bindings.get(port_name)
    if binding is None and result.stderr.strip():
      self._publish_log("system", None, None, "", result.stderr.strip())
    return binding

  def _request_bluetooth_disconnect(self, address: str) -> None:
    self._run_bluetoothctl_command(
        f"disconnect {address}",
        f"requested Bluetooth disconnect from {address}",
    )

  def _request_bluetooth_connect(self, address: str) -> bool:
    self._run_bluetoothctl_command(
        f"connect {address}",
        f"requested Bluetooth reconnect to {address}",
    )
    connected = self._wait_for_bluetooth_connection_state(address, True)
    if connected:
      self._publish_log("system", None, None, "",
                        f"confirmed Bluetooth connected to {address}")
    else:
      self._publish_log("system", None, None, "",
                        f"Bluetooth connection to {address} was not confirmed")
    return connected

  def _wait_for_bluetooth_connection_state(self, address: str,
                                           expected_state: bool) -> bool:
    deadline = time.monotonic() + BLUETOOTH_CONNECTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
      connected = self._query_bluetooth_connection_state(address)
      if connected is expected_state:
        return True
      time.sleep(BLUETOOTH_STATE_POLL_INTERVAL_SECONDS)

    return False

  def _query_bluetooth_connection_state(self, address: str) -> Optional[bool]:
    try:
      result = self._run_command(["bluetoothctl"],
                                 input_text=f"info {address}\nquit\n")
    except (OSError, subprocess.SubprocessError) as exc:
      self._publish_log("system", None, None, "",
                        f"bluetoothctl info failed: {exc}")
      return None

    combined_output = self._combine_command_output(result.stdout, result.stderr)
    return parse_bluetooth_connected(combined_output)

  def _run_bluetoothctl_command(self, command: str, success_message: str) -> None:
    try:
      result = self._run_command(["bluetoothctl"],
                                 input_text=f"{command}\nquit\n")
    except (OSError, subprocess.SubprocessError) as exc:
      self._publish_log("system", None, None, "",
                        f"bluetoothctl failed: {exc}")
      return

    combined_output = self._combine_command_output(result.stdout, result.stderr)
    normalized_output = combined_output.lower()
    command_failed = result.returncode != 0 or "failed" in normalized_output
    command_failed = command_failed or "not available" in normalized_output
    command_failed = command_failed or "no default controller available" in normalized_output
    if command_failed:
      if combined_output:
        self._publish_log("system", None, None, "", combined_output)
      return

    if combined_output:
      self._publish_log("system", None, None, "", combined_output)
    self._publish_log("system", None, None, "", success_message)

  @staticmethod
  def _combine_command_output(stdout_text: str, stderr_text: str) -> str:
    combined_output = "\n".join(
        text.strip() for text in (stdout_text, stderr_text) if text.strip())
    return strip_ansi_escape_sequences(combined_output)

  def _warm_up_rfcomm_port(self) -> None:
    warmup_port = None
    try:
      warmup_port = self._open_connection_port(self._connection)
      self._publish_log("system", None, None, "",
                        "performed Bluetooth serial warm-up open")
      time.sleep(RFCOMM_RESET_SETTLE_SECONDS)
      self._reset_serial_port(warmup_port, allow_failure=True)
    except (OSError, SerialException) as exc:
      self._publish_log("system", None, None, "",
                        f"Bluetooth warm-up open failed: {exc}")
    finally:
      self._close_serial_port(warmup_port)
      time.sleep(RFCOMM_RESET_SETTLE_SECONDS)

  def _open_connection_port(self, connection: ConnectionConfig) -> ByteStreamPort:
    if isinstance(connection, BluetoothConnectionConfig):
      return BluetoothSocketPort(connection.address, connection.channel)

    if serial is None:
      raise SerialException(f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
    return serial.Serial(
        port=connection.port,
        baudrate=self._baud_rate,
        timeout=0.01,
        write_timeout=0.25,
    )

  def _connection_reset_settle_seconds(self) -> float:
    if isinstance(self._connection, BluetoothConnectionConfig):
      return 0.0
    return connection_reset_settle_seconds(self._connection.port)

  def _opened_connection_message(self) -> str:
    if isinstance(self._connection, BluetoothConnectionConfig):
      return (f"opened {self._connection.describe()} via Bluetooth RFCOMM "
              f"channel {self._connection.channel}")
    return f"opened {self._connection.describe()} at {self._baud_rate} baud"

  def _reset_serial_port(self,
                         serial_port: ByteStreamPort,
                         allow_failure: bool = False) -> None:
    try:
      serial_port.reset_input_buffer()
      serial_port.reset_output_buffer()
    except Exception as exc:
      if allow_failure:
        self._publish_log("system", None, None, "",
                          f"serial buffer reset skipped: {exc}")
        return
      raise SerialException(f"serial buffer reset failed: {exc}") from exc

  @staticmethod
  def _close_serial_port(serial_port: Optional[ByteStreamPort]) -> None:
    if serial_port is None:
      return

    try:
      serial_port.close()
    except (OSError, SerialException):
      pass

  def _read_available_bytes(self, serial_port: ByteStreamPort) -> bytes:
    bytes_waiting = getattr(serial_port, "in_waiting", 0)
    read_size = bytes_waiting if bytes_waiting > 0 else 1
    return bytes(serial_port.read(read_size))

  def _perform_connection_handshake(self,
                                    serial_port: ByteStreamPort,
                                    allow_timeout: bool = False) -> bool:
    deadline = time.monotonic() + CONNECTION_HANDSHAKE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
      if self._stop_requested.is_set():
        raise SerialException("connection cancelled")

      self._send_current_command(serial_port)
      if self._process_incoming_bytes(serial_port):
        return True

      time.sleep(CONNECTION_HANDSHAKE_RETRY_SECONDS)

    if allow_timeout:
      self._publish_log(
          "system",
          None,
          None,
          "",
          "telemetry handshake timed out; keeping serial link open",
      )
      return False

    raise SerialException("connection handshake failed: no telemetry received")

  def _process_incoming_bytes(self, serial_port: ByteStreamPort) -> bool:
    incoming_bytes = self._read_available_bytes(serial_port)
    if not incoming_bytes:
      return False

    parsed_frames, parse_errors = self._codec.feed_bytes(incoming_bytes)
    for error_event in parse_errors:
      self._invalid_frame_count += 1
      self._publish_log(
          "rx",
          error_event.message_type,
          error_event.sequence,
          error_event.hex_string,
          error_event.status,
      )
      self._publish_status(self._link_active, True, "received invalid frame")

    received_telemetry = False
    for parsed_frame in parsed_frames:
      self._rx_count += 1
      self._publish_log(
          "rx",
          parsed_frame.message_type,
          parsed_frame.sequence,
          ProtocolCodec.bytes_to_hex(parsed_frame.frame_bytes),
          "ok",
      )
      if parsed_frame.message_type != ProtocolCodec.MESSAGE_TYPE_TELEMETRY_SNAPSHOT:
        self._publish_log(
            "rx",
            parsed_frame.message_type,
            parsed_frame.sequence,
            "",
            "unknown message type",
        )
        self._publish_status(self._link_active, True, "received unknown frame")
        continue

      try:
        snapshot = self._codec.decode_telemetry_payload(parsed_frame.payload)
      except ValueError as exc:
        self._invalid_frame_count += 1
        self._publish_log(
            "rx",
            parsed_frame.message_type,
            parsed_frame.sequence,
            "",
            f"decode error: {exc}",
        )
        self._publish_status(self._link_active, True, "telemetry decode error")
        continue

      self._protocol_ready = True
      self._event_queue.put(snapshot)
      self._publish_status(self._link_active, True, "telemetry updated")
      received_telemetry = True

    return received_telemetry

  def _send_current_command(self, serial_port: ByteStreamPort) -> None:
    with self._command_lock:
      command = ActuatorCommand(self._current_command.servo_angle_degrees,
                                self._current_command.vibration_enabled)
    self._write_command(serial_port, command, log_status="ok")
    self._command_dirty.clear()

  def _write_command(self, serial_port: ByteStreamPort, command: ActuatorCommand,
                     log_status: str) -> None:
    frame = self._codec.encode_actuator_command_frame(command, self._tx_sequence)
    written = serial_port.write(frame)
    if written != len(frame):
      raise SerialException(
          f"short write: expected {len(frame)} bytes, wrote {written}")
    self._publish_log("tx",
                      ProtocolCodec.MESSAGE_TYPE_ACTUATOR_COMMAND,
                      self._tx_sequence, ProtocolCodec.bytes_to_hex(frame),
                      log_status)
    self._tx_sequence = (self._tx_sequence + 1) & 0xFF
    self._tx_count += 1
    status_detail = "command sent"
    if self._link_active and not self._protocol_ready:
      status_detail = "command sent, awaiting telemetry"
    self._publish_status(self._link_active, True, status_detail)

  def _publish_log(self, direction: str, message_type: Optional[int],
                   sequence: Optional[int], hex_string: str,
                   status: str) -> None:
    self._event_queue.put(
        RawFrameEvent(
            direction=direction,
            message_type=message_type,
            sequence=sequence,
            hex_string=hex_string,
            timestamp=time.time(),
            status=status,
        ))

  def _publish_status(self, connected: bool, keepalive_active: bool,
                      detail: str) -> None:
    self._event_queue.put(
        WorkerStatus(
            connected=connected,
            keepalive_active=keepalive_active,
            tx_count=self._tx_count,
            rx_count=self._rx_count,
            invalid_frame_count=self._invalid_frame_count,
            connection_label=self._connection.describe(),
            detail=detail,
        ))


class TesterApp:
  POLL_INTERVAL_MS = 50

  def __init__(self, root: tk.Tk, default_port: str, baud_rate: int,
               keepalive_ms: int, log_lines: int) -> None:
    self._root = root
    self._baud_rate = baud_rate
    self._keepalive_ms = keepalive_ms
    self._max_log_lines = log_lines
    self._event_queue: "queue.Queue[object]" = queue.Queue()
    self._worker: Optional[SerialWorker] = None
    self._last_status: Optional[WorkerStatus] = None
    self._log_line_count = 0

    self._port_var = tk.StringVar(value=default_port)
    self._baud_var = tk.StringVar(value=str(baud_rate))
    self._connection_hint_var = tk.StringVar()
    self._servo_var = tk.DoubleVar(value=NEUTRAL_SERVO_ANGLE)
    self._servo_entry_var = tk.StringVar(value=f"{NEUTRAL_SERVO_ANGLE:.1f}")
    self._vibration_var = tk.BooleanVar(value=False)

    self._distance_var = tk.StringVar(value="--")
    self._distance_flags_var = tk.StringVar(value="valid: -- | timeout: --")
    self._accel_x_var = tk.StringVar(value="--")
    self._accel_y_var = tk.StringVar(value="--")
    self._accel_z_var = tk.StringVar(value="--")
    self._joystick_x_var = tk.StringVar(value="--")
    self._joystick_y_var = tk.StringVar(value="--")
    self._joystick_button_var = tk.StringVar(value="--")
    self._last_rx_var = tk.StringVar(value="--")
    self._status_bar_var = tk.StringVar(
        value="Disconnected | Keepalive: Off | TX: 0 | RX: 0 | Invalid: 0")

    self._build_ui()
    self._root.protocol("WM_DELETE_WINDOW", self._on_close_requested)
    self.refresh_ports()
    self._set_connection_controls(is_connected=False)
    self._schedule_poll()

  def refresh_ports(self) -> None:
    ports = self._enumerate_ports()
    self._port_combo["values"] = ports
    if not self._port_var.get() and ports:
      self._port_var.set(ports[0])

  def connect(self) -> None:
    if self._worker is not None and self._worker.is_alive():
      return

    try:
      connection = build_connection_config(self._port_var.get())
      connection = resolve_connection_config(connection)
      baud_rate = parse_baud_rate(self._baud_var.get())
    except ValueError as exc:
      messagebox.showerror("Connection Error", str(exc))
      return

    self._apply_command_to_widgets(NEUTRAL_SERVO_ANGLE, False)
    self._last_status = None
    self._worker = SerialWorker(connection, baud_rate, self._keepalive_ms,
                                self._event_queue)
    self._worker.start()
    connection_text = f"connecting to {connection.describe()}"
    if not isinstance(connection, BluetoothConnectionConfig):
      connection_text = f"{connection_text} at {baud_rate} baud"
    self._append_log_line("SYSTEM", "--", "--", "",
                          connection_text)
    self._set_connection_controls(is_connected=True)

  def disconnect(self) -> None:
    if self._worker is None:
      return

    self._worker.stop()
    self._append_log_line("SYSTEM", "--", "--", "", "disconnect requested")
    self._set_connection_controls(is_connected=False)

  def center_servo(self) -> None:
    self._apply_command_to_widgets(NEUTRAL_SERVO_ANGLE,
                                   self._vibration_var.get())

  def all_stop(self) -> None:
    self._apply_command_to_widgets(NEUTRAL_SERVO_ANGLE, False)

  def _build_ui(self) -> None:
    self._root.title("Arduino UART / Bluetooth Tester")
    self._root.geometry("1180x780")

    container = ttk.Frame(self._root, padding=12)
    container.grid(row=0, column=0, sticky="nsew")
    self._root.columnconfigure(0, weight=1)
    self._root.rowconfigure(0, weight=1)
    container.columnconfigure(0, weight=1)
    container.columnconfigure(1, weight=1)
    container.rowconfigure(2, weight=1)

    top_bar = ttk.Frame(container)
    top_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
    top_bar.columnconfigure(1, weight=1)
    top_bar.columnconfigure(3, weight=1)

    ttk.Label(top_bar, text="Port").grid(row=0, column=0, padx=(0, 8))
    self._port_combo = ttk.Combobox(
        top_bar,
        textvariable=self._port_var,
        state="normal",
    )
    self._port_combo.grid(row=0, column=1, sticky="ew", padx=(0, 12))
    self._refresh_button = ttk.Button(top_bar,
                                      text="Refresh",
                                      command=self.refresh_ports)
    self._refresh_button.grid(row=0, column=2, sticky="w")
    ttk.Label(top_bar, text="Baud").grid(row=0, column=3, padx=(12, 8))
    baud_values = [str(baud_rate) for baud_rate in COMMON_BAUD_RATES]
    if self._baud_var.get() not in baud_values:
      baud_values.append(self._baud_var.get())
    self._baud_combo = ttk.Combobox(
        top_bar,
        textvariable=self._baud_var,
        values=baud_values,
        state="normal",
        width=10,
    )
    self._baud_combo.grid(row=0, column=4, sticky="w")
    self._connect_button = ttk.Button(top_bar,
                                      text="Connect",
                                      command=self.connect)
    self._connect_button.grid(row=0, column=5, sticky="w", padx=(12, 0))
    self._disconnect_button = ttk.Button(top_bar,
                                         text="Disconnect",
                                         command=self.disconnect)
    self._disconnect_button.grid(row=0, column=6, sticky="w", padx=(12, 0))
    ttk.Label(top_bar,
              textvariable=self._connection_hint_var).grid(row=1,
                                                           column=0,
                                                           columnspan=7,
                                                           sticky="w",
                                                           pady=(8, 0))

    command_frame = ttk.LabelFrame(container, text="Command")
    command_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
    command_frame.columnconfigure(0, weight=1)

    self._servo_scale = tk.Scale(
        command_frame,
        from_=SERVO_MIN_ANGLE,
        to=SERVO_MAX_ANGLE,
        resolution=0.1,
        orient=tk.HORIZONTAL,
        label="Servo Angle (deg)",
        variable=self._servo_var,
        command=self._on_servo_scale_changed,
    )
    self._servo_scale.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(8, 8))

    ttk.Label(command_frame, text="Servo").grid(row=1, column=0, sticky="w")
    self._servo_spinbox = ttk.Spinbox(
        command_frame,
        from_=SERVO_MIN_ANGLE,
        to=SERVO_MAX_ANGLE,
        increment=0.1,
        textvariable=self._servo_entry_var,
        width=10,
    )
    self._servo_spinbox.grid(row=1, column=1, sticky="w", padx=(8, 8))
    self._servo_spinbox.bind("<Return>", self._on_servo_entry_committed)
    self._servo_spinbox.bind("<FocusOut>", self._on_servo_entry_committed)
    self._servo_spinbox.bind("<<Increment>>", self._on_servo_entry_committed)
    self._servo_spinbox.bind("<<Decrement>>", self._on_servo_entry_committed)

    self._vibration_check = ttk.Checkbutton(
        command_frame,
        text="Vibration Enabled",
        variable=self._vibration_var,
        command=self._on_vibration_changed,
    )
    self._vibration_check.grid(row=2, column=0, columnspan=2, sticky="w",
                               pady=(8, 8))

    ttk.Button(command_frame,
               text="Center Servo",
               command=self.center_servo).grid(row=3, column=0, sticky="ew")
    ttk.Button(command_frame, text="All Stop",
               command=self.all_stop).grid(row=3,
                                           column=1,
                                           sticky="ew",
                                           padx=(8, 0))

    telemetry_frame = ttk.LabelFrame(container, text="Telemetry")
    telemetry_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
    telemetry_frame.columnconfigure(1, weight=1)

    telemetry_rows = (
        ("Distance", self._distance_var),
        ("Distance Flags", self._distance_flags_var),
        ("Accel X", self._accel_x_var),
        ("Accel Y", self._accel_y_var),
        ("Accel Z", self._accel_z_var),
        ("Joystick X", self._joystick_x_var),
        ("Joystick Y", self._joystick_y_var),
        ("Joystick Button", self._joystick_button_var),
        ("Last RX", self._last_rx_var),
    )
    for index, (label, variable) in enumerate(telemetry_rows):
      ttk.Label(telemetry_frame, text=label).grid(row=index,
                                                  column=0,
                                                  sticky="w",
                                                  padx=(8, 12),
                                                  pady=4)
      ttk.Label(telemetry_frame, textvariable=variable).grid(row=index,
                                                             column=1,
                                                             sticky="w",
                                                             padx=(0, 8),
                                                             pady=4)

    log_frame = ttk.LabelFrame(container, text="Protocol Log")
    log_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(0, weight=1)

    self._log_text = ScrolledText(log_frame,
                                  wrap=tk.NONE,
                                  height=20,
                                  state=tk.DISABLED)
    self._log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    status_bar = ttk.Label(container,
                           textvariable=self._status_bar_var,
                           anchor="w")
    status_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

  def _enumerate_ports(self) -> list[str]:
    discovered_ports: list[str] = []
    if list_ports is not None:
      ports = sorted(list_ports.comports(), key=lambda port_info: port_info.device)
      discovered_ports.extend(port_info.device for port_info in ports)
    discovered_ports.extend(list_bluetooth_device_options())
    return list(dict.fromkeys(discovered_ports))

  def _schedule_poll(self) -> None:
    self._root.after(self.POLL_INTERVAL_MS, self._poll_worker_events)

  def _poll_worker_events(self) -> None:
    while True:
      try:
        event = self._event_queue.get_nowait()
      except queue.Empty:
        break

      if isinstance(event, TelemetrySnapshot):
        self._handle_telemetry_snapshot(event)
      elif isinstance(event, RawFrameEvent):
        self._handle_raw_frame_event(event)
      elif isinstance(event, WorkerStatus):
        self._handle_worker_status(event)

    if self._worker is not None and not self._worker.is_alive():
      self._worker = None
      self._set_connection_controls(is_connected=False)

    self._schedule_poll()

  def _handle_telemetry_snapshot(self, snapshot: TelemetrySnapshot) -> None:
    self._distance_var.set(
        f"{snapshot.distance_mm} mm" if snapshot.distance_valid else "0 mm")
    self._distance_flags_var.set(
        f"valid: {self._format_boolean(snapshot.distance_valid)} | "
        f"timeout: {self._format_boolean(snapshot.distance_timed_out)}")
    self._accel_x_var.set(self._format_axis(snapshot.accel_x_mg, "mg",
                                            snapshot.accel_valid))
    self._accel_y_var.set(self._format_axis(snapshot.accel_y_mg, "mg",
                                            snapshot.accel_valid))
    self._accel_z_var.set(self._format_axis(snapshot.accel_z_mg, "mg",
                                            snapshot.accel_valid))
    self._joystick_x_var.set(f"{snapshot.joystick_x_permille} permille")
    self._joystick_y_var.set(f"{snapshot.joystick_y_permille} permille")
    self._joystick_button_var.set(
        "Pressed" if snapshot.joystick_button_pressed else "Released")

  def _handle_raw_frame_event(self, event: RawFrameEvent) -> None:
    direction_label = event.direction.upper()
    sequence = "--" if event.sequence is None else f"{event.sequence:02X}"
    message_type = ProtocolCodec.format_message_type(event.message_type)
    self._append_log_line(direction_label, message_type, sequence,
                          event.hex_string, event.status)
    if event.direction.startswith("rx") and event.status == "ok":
      self._last_rx_var.set(self._format_timestamp(event.timestamp))

  def _handle_worker_status(self, status: WorkerStatus) -> None:
    self._last_status = status
    connection_state = f"{'Connected' if status.connected else 'Disconnected'}"
    keepalive_state = "On" if status.keepalive_active else "Off"
    port_label = status.connection_label if status.connection_label else "--"
    self._status_bar_var.set(
        f"{connection_state} ({port_label}) | Keepalive: {keepalive_state} | "
        f"TX: {status.tx_count} | RX: {status.rx_count} | "
        f"Invalid: {status.invalid_frame_count} | {status.detail}")
    self._set_connection_controls(is_connected=status.connected)

  def _append_log_line(self, direction: str, message_type: str, sequence: str,
                       hex_string: str, status: str) -> None:
    timestamp = self._format_timestamp(time.time())
    details = f"{timestamp} | {direction:<6} | type={message_type:<4} | seq={sequence:<2} | {status}"
    if hex_string:
      details = f"{details} | {hex_string}"

    self._log_text.configure(state=tk.NORMAL)
    self._log_text.insert(tk.END, details + "\n")
    self._log_line_count += 1
    if self._log_line_count > self._max_log_lines:
      self._log_text.delete("1.0", "2.0")
      self._log_line_count -= 1
    self._log_text.see(tk.END)
    self._log_text.configure(state=tk.DISABLED)

  def _set_connection_controls(self, is_connected: bool) -> None:
    self._connect_button.configure(state=tk.DISABLED if is_connected else tk.NORMAL)
    self._disconnect_button.configure(state=tk.NORMAL if is_connected else tk.DISABLED)
    self._refresh_button.configure(state=tk.DISABLED if is_connected else tk.NORMAL)
    self._port_combo.configure(state=tk.DISABLED if is_connected else tk.NORMAL)
    self._baud_combo.configure(state=tk.DISABLED if is_connected else tk.NORMAL)
    self._connection_hint_var.set(
        ("Use one link for both commands and telemetry. "
         "Choose a serial port, `/dev/rfcommN`, or a Bluetooth device like "
         "`HC-05 [AA:BB:CC:DD:EE:FF]`."))

  def _apply_command_to_widgets(self, servo_angle: float,
                                vibration_enabled: bool) -> None:
    clamped_angle = ProtocolCodec.clamp_servo_angle(servo_angle)
    self._servo_var.set(clamped_angle)
    self._servo_entry_var.set(f"{clamped_angle:.1f}")
    self._vibration_var.set(vibration_enabled)
    self._push_command_to_worker()

  def _push_command_to_worker(self) -> None:
    if self._worker is None or not self._worker.is_alive():
      return

    self._worker.update_command(
        ActuatorCommand(
            servo_angle_degrees=ProtocolCodec.clamp_servo_angle(
                self._servo_var.get()),
            vibration_enabled=self._vibration_var.get(),
        ))

  def _on_servo_scale_changed(self, value: str) -> None:
    clamped_value = ProtocolCodec.clamp_servo_angle(float(value))
    self._servo_entry_var.set(f"{clamped_value:.1f}")
    self._push_command_to_worker()

  def _on_servo_entry_committed(self, _event: object) -> None:
    try:
      requested_value = float(self._servo_entry_var.get())
    except ValueError:
      requested_value = self._servo_var.get()

    clamped_value = ProtocolCodec.clamp_servo_angle(requested_value)
    self._servo_var.set(clamped_value)
    self._servo_entry_var.set(f"{clamped_value:.1f}")
    self._push_command_to_worker()

  def _on_vibration_changed(self) -> None:
    self._push_command_to_worker()

  def _on_close_requested(self) -> None:
    if self._worker is None or not self._worker.is_alive():
      self._root.destroy()
      return

    self.disconnect()
    self._wait_for_worker_shutdown()

  def _wait_for_worker_shutdown(self) -> None:
    if self._worker is None or not self._worker.is_alive():
      self._root.destroy()
      return
    self._root.after(100, self._wait_for_worker_shutdown)

  @staticmethod
  def _format_axis(value: int, unit: str, is_valid: bool) -> str:
    if not is_valid:
      return f"0 {unit} (invalid)"
    return f"{value} {unit}"

  @staticmethod
  def _format_boolean(value: bool) -> str:
    return "yes" if value else "no"

  @staticmethod
  def _format_timestamp(timestamp: float) -> str:
    local_time = time.localtime(timestamp)
    milliseconds = int((timestamp - int(timestamp)) * 1000.0)
    return time.strftime("%H:%M:%S", local_time) + f".{milliseconds:03d}"


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description="Tkinter UART/Bluetooth tester for the Arduino firmware protocol.")
  parser.add_argument("--port",
                      default="",
                      help="Serial port to prefill in the GUI.")
  parser.add_argument("--baud",
                      type=int,
                      default=DEFAULT_BAUD_RATE,
                      help=f"UART baud rate. Default: {DEFAULT_BAUD_RATE}.")
  parser.add_argument(
      "--keepalive-ms",
      type=int,
      default=DEFAULT_KEEPALIVE_MS,
      help=f"Continuous command resend period in milliseconds. Default: {DEFAULT_KEEPALIVE_MS}.")
  parser.add_argument("--log-lines",
                      type=int,
                      default=DEFAULT_LOG_LINES,
                      help=f"Maximum log lines to keep in the UI. Default: {DEFAULT_LOG_LINES}.")
  return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
  args = parse_args(argv)
  root = tk.Tk()
  TesterApp(
      root=root,
      default_port=args.port,
      baud_rate=args.baud,
      keepalive_ms=max(1, args.keepalive_ms),
      log_lines=max(10, args.log_lines),
  )
  root.mainloop()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
