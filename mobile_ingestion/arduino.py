from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol

from uart_protocol import (ARDUINO_RESET_SETTLE_SECONDS, DEFAULT_BAUD_RATE,
                           DEFAULT_KEEPALIVE_MS, NEUTRAL_SERVO_ANGLE,
                           ActuatorCommand, ProtocolCodec, RawFrameEvent,
                           TelemetrySnapshot)

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


DEFAULT_EVENT_LOG_CAPACITY = 500
DEFAULT_SUBSCRIBER_QUEUE_CAPACITY = 256
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class ArduinoUnavailableError(RuntimeError):
  pass


class ArduinoConflictError(RuntimeError):
  pass


class SerialPortPort(Protocol):
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

  def __init__(self, *, port: str, baud_rate: int, keepalive_ms: int,
               reset_settle_seconds: float,
               serial_factory: Callable[..., SerialPortPort],
               get_current_command: Callable[[], ActuatorCommand],
               on_status: Callable[[WorkerStatus], None],
               on_telemetry: Callable[[TelemetrySnapshot], None],
               on_frame: Callable[[RawFrameEvent], None],
               on_stopped: Callable[[], None]) -> None:
    super().__init__(daemon=True, name="mobile-ingestion-arduino-worker")
    self._port = port
    self._baud_rate = baud_rate
    self._keepalive_seconds = keepalive_ms / 1000.0
    self._reset_settle_seconds = reset_settle_seconds
    self._serial_factory = serial_factory
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
    self._command_dirty.set()

  def stop(self) -> None:
    self._stop_requested.set()

  def request_command_flush(self) -> None:
    self._command_dirty.set()

  def run(self) -> None:
    serial_port = None
    try:
      serial_port = self._serial_factory(
          port=self._port,
          baudrate=self._baud_rate,
          timeout=0.01,
          write_timeout=0.25,
      )
      time.sleep(self._reset_settle_seconds)
      serial_port.reset_input_buffer()
      serial_port.reset_output_buffer()
      self._publish_frame(
          RawFrameEvent(
              direction="system",
              message_type=None,
              sequence=None,
              hex_string="",
              timestamp=time.time(),
              status=f"opened serial port {self._port} and waited for reset",
          ))
      self._publish_status(True, True, f"connected to {self._port}")

      next_keepalive_deadline = time.monotonic()
      while not self._stop_requested.is_set():
        now = time.monotonic()
        if self._command_dirty.is_set() or now >= next_keepalive_deadline:
          self._write_command(
              serial_port,
              self._get_current_command(),
              log_status="ok",
          )
          self._command_dirty.clear()
          next_keepalive_deadline = time.monotonic() + self._keepalive_seconds

        incoming_bytes = self._read_available_bytes(serial_port)
        if not incoming_bytes:
          continue

        parsed_frames, parse_errors = self._codec.feed_bytes(incoming_bytes)
        for error_event in parse_errors:
          self._invalid_frame_count += 1
          self._publish_frame(error_event)
          self._publish_status(True, True, "received invalid frame")

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
            self._publish_status(True, True, "received unknown frame")
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
            self._publish_status(True, True, "telemetry decode error")
            continue

          self._on_telemetry(snapshot)
          self._publish_status(True, True, "telemetry updated")
    except (OSError, SerialException) as exc:
      self._publish_frame(
          RawFrameEvent(
              direction="system",
              message_type=None,
              sequence=None,
              hex_string="",
              timestamp=time.time(),
              status=f"serial error: {exc}",
          ))
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

        try:
          serial_port.close()
        except (OSError, SerialException):
          pass

      self._publish_frame(
          RawFrameEvent(
              direction="system",
              message_type=None,
              sequence=None,
              hex_string="",
              timestamp=time.time(),
              status="serial worker stopped",
          ))
      self._publish_status(False, False, "disconnected")
      self._on_stopped()

  def _read_available_bytes(self, serial_port: SerialPortPort) -> bytes:
    bytes_waiting = getattr(serial_port, "in_waiting", 0)
    read_size = bytes_waiting if bytes_waiting > 0 else 1
    return bytes(serial_port.read(read_size))

  def _write_command(self, serial_port: SerialPortPort,
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
    self._publish_status(True, True, "command sent")

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
      serial_factory: Callable[..., SerialPortPort] | None = None,
      ports_lister: Callable[[], tuple[str, ...]] | None = None,
  ) -> None:
    self._baud_rate = baud_rate
    self._keepalive_ms = keepalive_ms
    self._reset_settle_seconds = reset_settle_seconds
    self._shutdown_timeout_seconds = shutdown_timeout_seconds
    self._serial_factory = serial_factory or self._default_serial_factory
    self._ports_lister = ports_lister or self._default_ports_lister
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
    if serial is None:
      return ()
    return self._ports_lister()

  def connect(self, port: str) -> None:
    port_name = port.strip()
    if not port_name:
      raise ValueError("Field 'port' must be a non-empty string.")
    if serial is None:
      raise ArduinoUnavailableError(self._default_detail())

    worker = _SerialWorker(
        port=port_name,
        baud_rate=self._baud_rate,
        keepalive_ms=self._keepalive_ms,
        reset_settle_seconds=self._reset_settle_seconds,
        serial_factory=self._serial_factory,
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
        available=serial is not None,
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
    command = self._debug_command if self._debug_enabled else self._backend_command
    return ProtocolCodec.clamp_command(command)

  def _default_detail(self) -> str:
    if SERIAL_IMPORT_ERROR is not None:
      return f"pyserial unavailable: {SERIAL_IMPORT_ERROR}"
    return "serial ready"

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
  def _default_serial_factory(**kwargs: object) -> SerialPortPort:
    if serial is None:
      raise ArduinoUnavailableError(
          f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
    return serial.Serial(**kwargs)

  @staticmethod
  def _default_ports_lister() -> tuple[str, ...]:
    if list_ports is None:
      return ()
    ports = sorted(list_ports.comports(), key=lambda port_info: port_info.device)
    return tuple(port_info.device for port_info in ports)
