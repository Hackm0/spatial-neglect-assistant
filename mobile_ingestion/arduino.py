from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from arduino_transport import (BLUETOOTH_CONNECTION_TIMEOUT_SECONDS,
                               BLUETOOTHCTL_TIMEOUT_SECONDS,
                               BLUETOOTH_STATE_POLL_INTERVAL_SECONDS,
                               BluetoothConnectionConfig, BluetoothSocketPort,
                               ByteStreamPort, ConnectionConfig,
                               RFCOMM_RESET_SETTLE_SECONDS,
                               RfcommBinding,
                               build_connection_config,
                               connection_reset_settle_seconds,
                               is_bluetooth_connection,
                               is_bluetooth_socket_available, is_rfcomm_port,
                               list_connection_options,
                               parse_bluetooth_connected,
                               parse_rfcomm_bindings, resolve_connection_config,
                               strip_ansi_escape_sequences)
from uart_protocol import (ARDUINO_RESET_SETTLE_SECONDS, DEFAULT_BAUD_RATE,
                           DEFAULT_KEEPALIVE_MS, NEUTRAL_SERVO_ANGLE,
                           ActuatorCommand, ProtocolCodec, RawFrameEvent,
                           TelemetrySnapshot)

try:
  import serial
  from serial import SerialException
except ImportError as exc:  # pragma: no cover - exercised only on missing deps
  serial = None
  SerialException = OSError
  SERIAL_IMPORT_ERROR = exc
else:
  SERIAL_IMPORT_ERROR = None


DEFAULT_EVENT_LOG_CAPACITY = 500
DEFAULT_SUBSCRIBER_QUEUE_CAPACITY = 256
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 0.01
DEFAULT_WRITE_TIMEOUT_SECONDS = 0.25
CONNECTION_HANDSHAKE_TIMEOUT_SECONDS = 4.0
CONNECTION_HANDSHAKE_RETRY_SECONDS = 0.1


class ArduinoUnavailableError(RuntimeError):
  pass


class ArduinoConflictError(RuntimeError):
  pass


@dataclass(frozen=True, slots=True)
class WorkerStatus:
  connected: bool
  keepalive_active: bool
  tx_count: int
  rx_count: int
  invalid_frame_count: int
  detail: str
  last_rx_timestamp: float | None


@dataclass(frozen=True, slots=True)
class ArduinoSnapshot:
  available: bool
  connected: bool
  keepalive_active: bool
  selected_port: str | None
  baud_rate: int
  tx_count: int
  rx_count: int
  invalid_frame_count: int
  detail: str
  debug_enabled: bool
  backend_command: ActuatorCommand
  debug_command: ActuatorCommand
  effective_command: ActuatorCommand
  latest_telemetry: TelemetrySnapshot | None
  last_rx_timestamp: float | None
  recent_frames: tuple[RawFrameEvent, ...]


@dataclass(frozen=True, slots=True)
class ArduinoEvent:
  event_type: str
  payload: object


@dataclass(frozen=True, slots=True)
class ArduinoSubscription:
  identifier: int
  events: "queue.Queue[ArduinoEvent | None]"


class ArduinoControllerPort(Protocol):

  def list_ports(self) -> tuple[str, ...]:
    raise NotImplementedError

  def connect(self, port: str) -> None:
    raise NotImplementedError

  def disconnect(self) -> None:
    raise NotImplementedError

  def shutdown(self) -> None:
    raise NotImplementedError

  def get_snapshot(self) -> ArduinoSnapshot:
    raise NotImplementedError

  def set_backend_command(self, command: ActuatorCommand) -> None:
    raise NotImplementedError

  def set_debug_enabled(self, enabled: bool) -> None:
    raise NotImplementedError

  def set_debug_command(self, command: ActuatorCommand) -> None:
    raise NotImplementedError

  def subscribe(self) -> ArduinoSubscription:
    raise NotImplementedError

  def unsubscribe(self, subscription: ArduinoSubscription) -> None:
    raise NotImplementedError


class _SerialWorker(threading.Thread):

  def __init__(self, *, connection: ConnectionConfig, baud_rate: int,
               keepalive_ms: int, serial_reset_settle_seconds: float,
               serial_factory: Callable[..., ByteStreamPort],
               bluetooth_port_factory: Callable[..., ByteStreamPort],
               get_current_command: Callable[[], ActuatorCommand],
               on_status: Callable[[WorkerStatus], None],
               on_telemetry: Callable[[TelemetrySnapshot], None],
               on_frame: Callable[[RawFrameEvent], None],
               on_stopped: Callable[[], None]) -> None:
    super().__init__(daemon=True, name="mobile-ingestion-arduino-worker")
    self._connection = connection
    self._baud_rate = baud_rate
    self._keepalive_seconds = keepalive_ms / 1000.0
    self._serial_reset_settle_seconds = serial_reset_settle_seconds
    self._serial_factory = serial_factory
    self._bluetooth_port_factory = bluetooth_port_factory
    self._get_current_command = get_current_command
    self._on_status = on_status
    self._on_telemetry = on_telemetry
    self._on_frame = on_frame
    self._on_stopped = on_stopped
    self._codec = ProtocolCodec()
    self._stop_requested = threading.Event()
    self._command_dirty = threading.Event()
    self._tx_sequence = 0
    self._tx_count = 0
    self._rx_count = 0
    self._invalid_frame_count = 0
    self._last_rx_timestamp: float | None = None
    self._link_active = False
    self._protocol_ready = False
    self._command_dirty.set()

  def stop(self) -> None:
    self._stop_requested.set()

  def request_command_flush(self) -> None:
    self._command_dirty.set()

  def run(self) -> None:
    if serial is None and not isinstance(self._connection,
                                         BluetoothConnectionConfig):
      self._publish_system_log(f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
      self._publish_status(False, False, "pyserial unavailable")
      self._on_stopped()
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
        time.sleep(settle_seconds)
      self._reset_serial_port(serial_port,
                              allow_failure=is_bluetooth_connection(
                                  self._connection))
      self._link_active = True
      self._command_dirty.set()
      self._publish_system_log(self._opened_connection_message())
      self._publish_status(True, True, "awaiting telemetry handshake")

      handshake_completed = self._perform_connection_handshake(
          serial_port, allow_timeout=is_bluetooth_connection(self._connection))
      if handshake_completed:
        self._publish_status(True, True, "connected")
      elif is_bluetooth_connection(self._connection):
        self._publish_status(True, True,
                             "Bluetooth link active, awaiting telemetry")

      next_keepalive_deadline = time.monotonic()
      while not self._stop_requested.is_set():
        now = time.monotonic()
        if self._command_dirty.is_set() or now >= next_keepalive_deadline:
          self._send_current_command(serial_port)
          next_keepalive_deadline = time.monotonic() + self._keepalive_seconds

        self._process_incoming_bytes(serial_port)
    except (OSError, SerialException) as exc:
      self._publish_system_log(f"serial error: {exc}")
      self._publish_status(False, False, f"serial error: {exc}")
    finally:
      if serial_port is not None:
        try:
          self._write_command(
              serial_port,
              ActuatorCommand(NEUTRAL_SERVO_ANGLE, False),
              log_status="disconnect safe command",
          )
        except (OSError, SerialException):
          pass

      self._close_serial_port(serial_port)
      self._cleanup_connection()
      self._link_active = False
      self._protocol_ready = False

      self._publish_system_log("serial worker stopped")
      self._publish_status(False, False, "disconnected")
      self._on_stopped()

  def _prepare_connection(self) -> None:
    if isinstance(self._connection, BluetoothConnectionConfig):
      self._publish_system_log(
          (f"opening direct Bluetooth RFCOMM link to "
           f"{self._connection.address} channel {self._connection.channel}"))
      return

    if not is_rfcomm_port(self._connection.port):
      return

    binding = self._lookup_rfcomm_binding(self._connection.port)
    if binding is None:
      self._publish_system_log(
          "rfcomm binding not found; continuing with direct port open")
      return

    self._publish_system_log(
        f"using bound Bluetooth port {binding.port} for {binding.address}")
    if "connected" not in binding.state.lower():
      self._request_bluetooth_connect(binding.address)
    self._warm_up_rfcomm_port()

  def _confirm_connection_ready(self) -> None:
    if isinstance(self._connection, BluetoothConnectionConfig):
      connected = self._query_bluetooth_connection_state(
          self._connection.address)
      if connected:
        self._publish_system_log(
            (f"confirmed direct Bluetooth RFCOMM link active on "
             f"{self._connection.address}"))
      else:
        self._publish_system_log(
            (f"opened direct Bluetooth RFCOMM socket to "
             f"{self._connection.address}"))
      return

    if not is_rfcomm_port(self._connection.port):
      return

    binding = self._lookup_rfcomm_binding(self._connection.port)
    if binding is not None and "connected" in binding.state.lower():
      self._publish_system_log(
          f"confirmed Bluetooth serial link active on {binding.port}: "
          f"{binding.state}")
      return

    self._publish_system_log(
        (f"RFCOMM state was not confirmed for {self._connection.port}; "
         "continuing with opened serial port"))

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
      self._publish_system_log(f"rfcomm lookup failed: {exc}")
      return None

    bindings = parse_rfcomm_bindings(result.stdout)
    binding = bindings.get(port_name)
    if binding is None and result.stderr.strip():
      self._publish_system_log(result.stderr.strip())
    return binding

  def _request_bluetooth_connect(self, address: str) -> bool:
    self._run_bluetoothctl_command(
        f"connect {address}",
        f"requested Bluetooth reconnect to {address}",
    )
    connected = self._wait_for_bluetooth_connection_state(address, True)
    if connected:
      self._publish_system_log(f"confirmed Bluetooth connected to {address}")
    else:
      self._publish_system_log(
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
      self._publish_system_log(f"bluetoothctl info failed: {exc}")
      return None

    combined_output = self._combine_command_output(result.stdout, result.stderr)
    return parse_bluetooth_connected(combined_output)

  def _run_bluetoothctl_command(self, command: str, success_message: str) -> None:
    try:
      result = self._run_command(["bluetoothctl"],
                                 input_text=f"{command}\nquit\n")
    except (OSError, subprocess.SubprocessError) as exc:
      self._publish_system_log(f"bluetoothctl failed: {exc}")
      return

    combined_output = self._combine_command_output(result.stdout, result.stderr)
    normalized_output = combined_output.lower()
    command_failed = result.returncode != 0 or "failed" in normalized_output
    command_failed = command_failed or "not available" in normalized_output
    command_failed = command_failed or "no default controller available" in normalized_output
    if command_failed:
      if combined_output:
        self._publish_system_log(combined_output)
      return

    if combined_output:
      self._publish_system_log(combined_output)
    self._publish_system_log(success_message)

  @staticmethod
  def _combine_command_output(stdout_text: str, stderr_text: str) -> str:
    combined_output = "\n".join(
        text.strip() for text in (stdout_text, stderr_text) if text.strip())
    return strip_ansi_escape_sequences(combined_output)

  def _warm_up_rfcomm_port(self) -> None:
    warmup_port = None
    try:
      warmup_port = self._open_connection_port(self._connection)
      self._publish_system_log("performed Bluetooth serial warm-up open")
      time.sleep(RFCOMM_RESET_SETTLE_SECONDS)
      self._reset_serial_port(warmup_port, allow_failure=True)
    except (OSError, SerialException) as exc:
      self._publish_system_log(f"Bluetooth warm-up open failed: {exc}")
    finally:
      self._close_serial_port(warmup_port)
      time.sleep(RFCOMM_RESET_SETTLE_SECONDS)

  def _open_connection_port(self, connection: ConnectionConfig) -> ByteStreamPort:
    if isinstance(connection, BluetoothConnectionConfig):
      return self._bluetooth_port_factory(
          address=connection.address,
          channel=connection.channel,
          timeout_seconds=DEFAULT_READ_TIMEOUT_SECONDS,
          write_timeout_seconds=DEFAULT_WRITE_TIMEOUT_SECONDS,
      )

    if serial is None:
      raise SerialException(f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
    return self._serial_factory(
        port=connection.port,
        baudrate=self._baud_rate,
        timeout=DEFAULT_READ_TIMEOUT_SECONDS,
        write_timeout=DEFAULT_WRITE_TIMEOUT_SECONDS,
    )

  def _connection_reset_settle_seconds(self) -> float:
    if isinstance(self._connection, BluetoothConnectionConfig):
      return 0.0
    if is_rfcomm_port(self._connection.port):
      return connection_reset_settle_seconds(self._connection.port)
    return self._serial_reset_settle_seconds

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
        self._publish_system_log(f"serial buffer reset skipped: {exc}")
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
      self._publish_system_log(
          "telemetry handshake timed out; keeping serial link open")
      return False

    raise SerialException("connection handshake failed: no telemetry received")

  def _process_incoming_bytes(self, serial_port: ByteStreamPort) -> bool:
    incoming_bytes = self._read_available_bytes(serial_port)
    if not incoming_bytes:
      return False

    parsed_frames, parse_errors = self._codec.feed_bytes(incoming_bytes)
    for error_event in parse_errors:
      self._invalid_frame_count += 1
      self._publish_frame(error_event)
      self._publish_status(self._link_active, True, "received invalid frame")

    received_telemetry = False
    for parsed_frame in parsed_frames:
      self._rx_count += 1
      self._last_rx_timestamp = time.time()
      self._publish_frame(
          RawFrameEvent(
              direction="rx",
              message_type=parsed_frame.message_type,
              sequence=parsed_frame.sequence,
              hex_string=ProtocolCodec.bytes_to_hex(parsed_frame.frame_bytes),
              timestamp=self._last_rx_timestamp,
              status="ok",
          ))
      if parsed_frame.message_type != ProtocolCodec.MESSAGE_TYPE_TELEMETRY_SNAPSHOT:
        self._publish_frame(
            RawFrameEvent(
                direction="rx",
                message_type=parsed_frame.message_type,
                sequence=parsed_frame.sequence,
                hex_string="",
                timestamp=time.time(),
                status="unknown message type",
            ))
        self._publish_status(self._link_active, True, "received unknown frame")
        continue

      try:
        snapshot = self._codec.decode_telemetry_payload(parsed_frame.payload)
      except ValueError as exc:
        self._invalid_frame_count += 1
        self._publish_frame(
            RawFrameEvent(
                direction="rx",
                message_type=parsed_frame.message_type,
                sequence=parsed_frame.sequence,
                hex_string="",
                timestamp=time.time(),
                status=f"decode error: {exc}",
            ))
        self._publish_status(self._link_active, True, "telemetry decode error")
        continue

      self._protocol_ready = True
      self._on_telemetry(snapshot)
      self._publish_status(self._link_active, True, "telemetry updated")
      received_telemetry = True

    return received_telemetry

  def _send_current_command(self, serial_port: ByteStreamPort) -> None:
    command = self._get_current_command()
    self._write_command(serial_port, command, log_status="ok")
    self._command_dirty.clear()

  def _write_command(self, serial_port: ByteStreamPort,
                     command: ActuatorCommand, log_status: str) -> None:
    frame = self._codec.encode_actuator_command_frame(command, self._tx_sequence)
    written = serial_port.write(frame)
    if written != len(frame):
      raise SerialException(
          f"short write: expected {len(frame)} bytes, wrote {written}")
    self._publish_frame(
        RawFrameEvent(
            direction="tx",
            message_type=ProtocolCodec.MESSAGE_TYPE_ACTUATOR_COMMAND,
            sequence=self._tx_sequence,
            hex_string=ProtocolCodec.bytes_to_hex(frame),
            timestamp=time.time(),
            status=log_status,
        ))
    self._tx_sequence = (self._tx_sequence + 1) & 0xFF
    self._tx_count += 1
    status_detail = "command sent"
    if self._link_active and not self._protocol_ready:
      status_detail = "command sent, awaiting telemetry"
    self._publish_status(self._link_active, True, status_detail)

  def _publish_system_log(self, status: str) -> None:
    self._publish_frame(
        RawFrameEvent(
            direction="system",
            message_type=None,
            sequence=None,
            hex_string="",
            timestamp=time.time(),
            status=status,
        ))

  def _publish_frame(self, event: RawFrameEvent) -> None:
    self._on_frame(event)

  def _publish_status(self, connected: bool, keepalive_active: bool,
                      detail: str) -> None:
    self._on_status(
        WorkerStatus(
            connected=connected,
            keepalive_active=keepalive_active,
            tx_count=self._tx_count,
            rx_count=self._rx_count,
            invalid_frame_count=self._invalid_frame_count,
            detail=detail,
            last_rx_timestamp=self._last_rx_timestamp,
        ))


class PySerialArduinoController(ArduinoControllerPort):

  def __init__(
      self,
      *,
      baud_rate: int = DEFAULT_BAUD_RATE,
      keepalive_ms: int = DEFAULT_KEEPALIVE_MS,
      reset_settle_seconds: float = ARDUINO_RESET_SETTLE_SECONDS,
      log_capacity: int = DEFAULT_EVENT_LOG_CAPACITY,
      shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
      serial_factory: Callable[..., ByteStreamPort] | None = None,
      bluetooth_port_factory: Callable[..., ByteStreamPort] | None = None,
      ports_lister: Callable[[], tuple[str, ...]] | None = None,
      connection_builder: Callable[[str], ConnectionConfig] | None = None,
      connection_resolver: Callable[[ConnectionConfig],
                                    ConnectionConfig] | None = None,
      bluetooth_available_probe: Callable[[], bool] | None = None,
  ) -> None:
    self._baud_rate = baud_rate
    self._keepalive_ms = keepalive_ms
    self._reset_settle_seconds = reset_settle_seconds
    self._shutdown_timeout_seconds = shutdown_timeout_seconds
    self._serial_factory = serial_factory or self._default_serial_factory
    self._bluetooth_port_factory = (bluetooth_port_factory
                                    or self._default_bluetooth_port_factory)
    self._ports_lister = ports_lister or self._default_ports_lister
    self._connection_builder = connection_builder or build_connection_config
    self._connection_resolver = connection_resolver or resolve_connection_config
    self._bluetooth_available_probe = (bluetooth_available_probe
                                       or is_bluetooth_socket_available)
    self._lock = threading.Lock()
    self._selected_port: str | None = None
    self._connected = False
    self._keepalive_active = False
    self._tx_count = 0
    self._rx_count = 0
    self._invalid_frame_count = 0
    self._detail = self._default_detail()
    self._debug_enabled = False
    self._backend_command = ActuatorCommand(NEUTRAL_SERVO_ANGLE, False)
    self._debug_command = self._backend_command
    self._latest_telemetry: TelemetrySnapshot | None = None
    self._last_rx_timestamp: float | None = None
    self._recent_frames: deque[RawFrameEvent] = deque(maxlen=log_capacity)
    self._worker: _SerialWorker | None = None
    self._subscriptions: dict[int, "queue.Queue[ArduinoEvent | None]"] = {}
    self._next_subscription_id = 1

  def list_ports(self) -> tuple[str, ...]:
    return tuple(self._ports_lister())

  def connect(self, port: str) -> None:
    port_name = port.strip()
    if not port_name:
      raise ValueError("Field 'port' must be a non-empty string.")
    connection = self._connection_builder(port_name)
    resolved_connection = self._connection_resolver(connection)
    if isinstance(resolved_connection, BluetoothConnectionConfig):
      if not self._bluetooth_available_probe():
        raise ArduinoUnavailableError(
            "Bluetooth RFCOMM sockets are unavailable on this platform.")
    elif serial is None:
      raise ArduinoUnavailableError(self._default_detail())

    worker = _SerialWorker(
        connection=resolved_connection,
        baud_rate=self._baud_rate,
        keepalive_ms=self._keepalive_ms,
        serial_reset_settle_seconds=self._reset_settle_seconds,
        serial_factory=self._serial_factory,
        bluetooth_port_factory=self._bluetooth_port_factory,
        get_current_command=self._get_effective_command,
        on_status=self._handle_worker_status,
        on_telemetry=self._handle_telemetry,
        on_frame=self._handle_frame,
        on_stopped=self._handle_worker_stopped,
    )

    with self._lock:
      if self._worker is not None and self._worker.is_alive():
        raise ArduinoConflictError("The Arduino connection is already active.")
      self._worker = worker
      self._selected_port = port_name
      self._connected = False
      self._keepalive_active = False
      self._tx_count = 0
      self._rx_count = 0
      self._invalid_frame_count = 0
      self._detail = f"connecting to {port_name}"
      self._latest_telemetry = None
      self._last_rx_timestamp = None
      self._recent_frames.clear()

    self._publish_status_event()
    worker.start()

  def disconnect(self) -> None:
    with self._lock:
      worker = self._worker
    if worker is None:
      with self._lock:
        self._connected = False
        self._keepalive_active = False
        self._detail = "disconnected"
      self._publish_status_event()
      return

    worker.stop()
    worker.join(timeout=self._shutdown_timeout_seconds)
    if worker.is_alive():
      raise RuntimeError("Timed out while stopping the Arduino worker.")

  def shutdown(self) -> None:
    self.disconnect()
    with self._lock:
      subscriptions = list(self._subscriptions.values())
      self._subscriptions.clear()
    for subscriber_queue in subscriptions:
      self._offer_to_queue(subscriber_queue, None)

  def get_snapshot(self) -> ArduinoSnapshot:
    with self._lock:
      return self._snapshot_locked()

  def set_backend_command(self, command: ActuatorCommand) -> None:
    with self._lock:
      self._backend_command = ProtocolCodec.clamp_command(command)
    self._notify_command_changed()
    self._publish_status_event()

  def set_debug_enabled(self, enabled: bool) -> None:
    with self._lock:
      self._debug_enabled = enabled
      if not enabled:
        self._debug_command = self._backend_command
    self._notify_command_changed()
    self._publish_status_event()

  def set_debug_command(self, command: ActuatorCommand) -> None:
    with self._lock:
      if not self._debug_enabled:
        raise ArduinoConflictError("Debug mode must be enabled first.")
      if not self._connected:
        raise ArduinoConflictError("Connect to the Arduino before sending a manual command.")
      self._debug_command = ProtocolCodec.clamp_command(command)
    self._notify_command_changed()
    self._publish_status_event()

  def subscribe(self) -> ArduinoSubscription:
    subscription_queue: "queue.Queue[ArduinoEvent | None]" = queue.Queue(
        maxsize=DEFAULT_SUBSCRIBER_QUEUE_CAPACITY)
    with self._lock:
      subscription_id = self._next_subscription_id
      self._next_subscription_id += 1
      self._subscriptions[subscription_id] = subscription_queue
      snapshot = self._snapshot_locked()
    self._offer_to_queue(subscription_queue,
                         ArduinoEvent("status", snapshot))
    return ArduinoSubscription(subscription_id, subscription_queue)

  def unsubscribe(self, subscription: ArduinoSubscription) -> None:
    with self._lock:
      subscription_queue = self._subscriptions.pop(subscription.identifier, None)
    if subscription_queue is not None:
      self._offer_to_queue(subscription_queue, None)

  def _get_effective_command(self) -> ActuatorCommand:
    with self._lock:
      return self._effective_command_locked()

  def _handle_worker_status(self, status: WorkerStatus) -> None:
    with self._lock:
      self._connected = status.connected
      self._keepalive_active = status.keepalive_active
      self._tx_count = status.tx_count
      self._rx_count = status.rx_count
      self._invalid_frame_count = status.invalid_frame_count
      self._detail = status.detail
      self._last_rx_timestamp = status.last_rx_timestamp
    self._publish_status_event()

  def _handle_telemetry(self, snapshot: TelemetrySnapshot) -> None:
    with self._lock:
      self._latest_telemetry = snapshot
    self._publish_event(ArduinoEvent("telemetry", snapshot))
    self._publish_status_event()

  def _handle_frame(self, event: RawFrameEvent) -> None:
    with self._lock:
      self._recent_frames.append(event)
      if event.direction == "rx" and event.status == "ok":
        self._last_rx_timestamp = event.timestamp
    self._publish_event(ArduinoEvent("frame", event))

  def _handle_worker_stopped(self) -> None:
    with self._lock:
      self._worker = None

  def _publish_status_event(self) -> None:
    self._publish_event(ArduinoEvent("status", self.get_snapshot()))

  def _publish_event(self, event: ArduinoEvent) -> None:
    with self._lock:
      subscribers = list(self._subscriptions.values())
    for subscriber_queue in subscribers:
      self._offer_to_queue(subscriber_queue, event)

  def _notify_command_changed(self) -> None:
    with self._lock:
      worker = self._worker
    if worker is not None:
      worker.request_command_flush()

  def _snapshot_locked(self) -> ArduinoSnapshot:
    return ArduinoSnapshot(
        available=self._transport_available(),
        connected=self._connected,
        keepalive_active=self._keepalive_active,
        selected_port=self._selected_port,
        baud_rate=self._baud_rate,
        tx_count=self._tx_count,
        rx_count=self._rx_count,
        invalid_frame_count=self._invalid_frame_count,
        detail=self._detail,
        debug_enabled=self._debug_enabled,
        backend_command=self._backend_command,
        debug_command=self._debug_command,
        effective_command=self._effective_command_locked(),
        latest_telemetry=self._latest_telemetry,
        last_rx_timestamp=self._last_rx_timestamp,
        recent_frames=tuple(self._recent_frames),
    )

  def _effective_command_locked(self) -> ActuatorCommand:
    if not self._debug_enabled:
      return ProtocolCodec.clamp_command(self._backend_command)

    # In debug mode, keep manual servo control but still allow backend
    # vibration bursts (object detection feedback) to reach the actuator.
    return ProtocolCodec.clamp_command(
        ActuatorCommand(
            servo_angle_degrees=self._debug_command.servo_angle_degrees,
            vibration_enabled=(self._debug_command.vibration_enabled
                               or self._backend_command.vibration_enabled),
        ))

  def _default_detail(self) -> str:
    if serial is not None:
      return "serial ready"
    if self._bluetooth_available_probe():
      return "Bluetooth RFCOMM ready"
    if SERIAL_IMPORT_ERROR is not None:
      return f"pyserial unavailable: {SERIAL_IMPORT_ERROR}"
    return "serial unavailable"

  def _transport_available(self) -> bool:
    return serial is not None or self._bluetooth_available_probe()

  @staticmethod
  def _offer_to_queue(queue_ref: "queue.Queue[ArduinoEvent | None]",
                      item: ArduinoEvent | None) -> None:
    try:
      queue_ref.put_nowait(item)
    except queue.Full:
      try:
        queue_ref.get_nowait()
      except queue.Empty:
        return
      queue_ref.put_nowait(item)

  @staticmethod
  def _default_serial_factory(**kwargs: object) -> ByteStreamPort:
    if serial is None:
      raise ArduinoUnavailableError(
          f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
    return serial.Serial(**kwargs)

  @staticmethod
  def _default_bluetooth_port_factory(address: str, channel: int,
                                      *,
                                      timeout_seconds: float,
                                      write_timeout_seconds: float
                                      ) -> ByteStreamPort:
    return BluetoothSocketPort(address, channel, timeout_seconds,
                               write_timeout_seconds)

  @staticmethod
  def _default_ports_lister() -> tuple[str, ...]:
    return list_connection_options()
