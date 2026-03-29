from __future__ import annotations

import re
import select
import socket
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Protocol

from uart_protocol import ARDUINO_RESET_SETTLE_SECONDS

try:
  from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised only on missing deps
  list_ports = None


DEFAULT_BLUETOOTH_RFCOMM_CHANNEL = 1
RFCOMM_PORT_PREFIX = "/dev/rfcomm"
RFCOMM_RESET_SETTLE_SECONDS = 0.35
BLUETOOTHCTL_TIMEOUT_SECONDS = 5.0
BLUETOOTH_CONNECTION_TIMEOUT_SECONDS = 4.0
BLUETOOTH_STATE_POLL_INTERVAL_SECONDS = 0.2
BLUETOOTH_SOCKET_CONNECT_TIMEOUT_SECONDS = 6.0
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


def is_rfcomm_port(port_name: str) -> bool:
  return port_name.startswith(RFCOMM_PORT_PREFIX)


def is_bluetooth_connection(connection: ConnectionConfig) -> bool:
  if isinstance(connection, BluetoothConnectionConfig):
    return True
  return is_rfcomm_port(connection.port)


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


def is_bluetooth_socket_available() -> bool:
  return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


class BluetoothSocketPort:
  _READY_READ_SIZE = 4096

  def __init__(self,
               address: str,
               channel: int,
               timeout_seconds: float = 0.01,
               write_timeout_seconds: float = 0.25) -> None:
    if not is_bluetooth_socket_available():
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


def run_command(arguments: list[str],
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


def _combine_command_output(stdout_text: str, stderr_text: str) -> str:
  combined_output = "\n".join(
      text.strip() for text in (stdout_text, stderr_text) if text.strip())
  return strip_ansi_escape_sequences(combined_output)


def list_bluetooth_device_options(
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> list[str]:
  runner = command_runner or run_command
  try:
    result = runner(["bluetoothctl", "devices"], timeout_seconds=2.0)
  except (OSError, subprocess.SubprocessError):
    return []

  combined_output = _combine_command_output(result.stdout, result.stderr)
  devices = parse_bluetooth_devices(combined_output)
  return [
      device.format_option() for device in sorted(
          devices.values(),
          key=lambda device: ((device.name or device.address).lower(),
                              device.address),
      )
  ]


def lookup_rfcomm_binding(
    port_name: str,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> Optional[RfcommBinding]:
  runner = command_runner or run_command
  try:
    result = runner(["rfcomm"], timeout_seconds=2.0)
  except (OSError, subprocess.SubprocessError):
    return None

  bindings = parse_rfcomm_bindings(result.stdout)
  return bindings.get(port_name)


def resolve_connection_config(
    connection: ConnectionConfig,
    *,
    binding_lookup: Callable[[str], Optional[RfcommBinding]] | None = None
) -> ConnectionConfig:
  if isinstance(connection, BluetoothConnectionConfig):
    return connection

  if not is_rfcomm_port(connection.port):
    return connection

  binding_resolver = binding_lookup or lookup_rfcomm_binding
  binding = binding_resolver(connection.port)
  if binding is None:
    return connection

  if not is_bluetooth_socket_available():
    return connection

  return BluetoothConnectionConfig(address=binding.address, channel=binding.channel)


def list_serial_port_options() -> list[str]:
  if list_ports is None:
    return []
  ports = sorted(list_ports.comports(), key=lambda port_info: port_info.device)
  return [port_info.device for port_info in ports]


def combine_connection_options(serial_options: Iterable[str],
                               bluetooth_options: Iterable[str]) -> tuple[str, ...]:
  return tuple(dict.fromkeys([*serial_options, *bluetooth_options]))


def list_connection_options(
    *,
    serial_options_lister: Callable[[], Iterable[str]] | None = None,
    bluetooth_options_lister: Callable[[], Iterable[str]] | None = None
) -> tuple[str, ...]:
  serial_options = (list_serial_port_options()
                    if serial_options_lister is None else
                    list(serial_options_lister()))
  bluetooth_options = (list_bluetooth_device_options()
                       if bluetooth_options_lister is None else
                       list(bluetooth_options_lister()))
  return combine_connection_options(serial_options, bluetooth_options)
