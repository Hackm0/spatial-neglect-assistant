#!/usr/bin/env python3
from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Iterable, Optional

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


DEFAULT_BAUD_RATE = 115200
DEFAULT_KEEPALIVE_MS = 50
DEFAULT_LOG_LINES = 500
ARDUINO_RESET_SETTLE_SECONDS = 2.0
NEUTRAL_SERVO_ANGLE = 90.0
SERVO_MIN_ANGLE = 0.0
SERVO_MAX_ANGLE = 180.0


@dataclass(slots=True)
class ActuatorCommand:
  servo_angle_degrees: float
  vibration_enabled: bool


@dataclass(slots=True)
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


@dataclass(slots=True)
class RawFrameEvent:
  direction: str
  message_type: Optional[int]
  sequence: Optional[int]
  hex_string: str
  timestamp: float
  status: str


@dataclass(slots=True)
class WorkerStatus:
  connected: bool
  keepalive_active: bool
  tx_count: int
  rx_count: int
  invalid_frame_count: int
  port: str
  detail: str


@dataclass(slots=True)
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
  def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)

  @staticmethod
  def format_message_type(message_type: Optional[int]) -> str:
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
    clamped_angle = self.clamp_servo_angle(command.servo_angle_degrees)
    servo_tenths = int((clamped_angle * 10.0) + 0.5)
    payload = bytearray(self.ACTUATOR_COMMAND_PAYLOAD_LENGTH)
    payload[0:2] = servo_tenths.to_bytes(2, byteorder="little", signed=False)
    payload[2] = (self.VIBRATION_ENABLED_FLAG_MASK
                  if command.vibration_enabled else 0)
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


class SerialWorker(threading.Thread):
  def __init__(self, port: str, baud_rate: int, keepalive_ms: int,
               event_queue: "queue.Queue[object]") -> None:
    super().__init__(daemon=True, name="uart-tester-serial-worker")
    self._port = port
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
    if serial is None:
      self._publish_log("system", None, None, "",
                        f"pyserial unavailable: {SERIAL_IMPORT_ERROR}")
      self._publish_status(False, False, "pyserial unavailable")
      return

    serial_port = None
    try:
      serial_port = serial.Serial(
          port=self._port,
          baudrate=self._baud_rate,
          timeout=0.01,
          write_timeout=0.25,
      )
      # Opening the Mega USB serial port resets the board. Give the bootloader
      # time to finish so we start from a clean protocol stream.
      time.sleep(ARDUINO_RESET_SETTLE_SECONDS)
      serial_port.reset_input_buffer()
      serial_port.reset_output_buffer()
      self._command_dirty.set()
      self._publish_log("system", None, None, "",
                        f"opened serial port {self._port} and waited for reset")
      self._publish_status(True, True, f"connected to {self._port}")

      next_keepalive_deadline = time.monotonic()
      while not self._stop_requested.is_set():
        now = time.monotonic()
        if self._command_dirty.is_set() or now >= next_keepalive_deadline:
          self._send_current_command(serial_port)
          next_keepalive_deadline = time.monotonic() + self._keepalive_seconds

        incoming_bytes = self._read_available_bytes(serial_port)
        if incoming_bytes:
          parsed_frames, parse_errors = self._codec.feed_bytes(incoming_bytes)
          for error_event in parse_errors:
            self._invalid_frame_count += 1
            self._event_queue.put(error_event)
            self._publish_status(True, True, "received invalid frame")

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
              self._publish_status(True, True, "received unknown frame")
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
              self._publish_status(True, True, "telemetry decode error")
              continue

            self._event_queue.put(snapshot)
            self._publish_status(True, True, "telemetry updated")
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

        try:
          serial_port.close()
        except (OSError, SerialException):
          pass

      self._publish_log("system", None, None, "", "serial worker stopped")
      self._publish_status(False, False, "disconnected")

  def _read_available_bytes(self, serial_port: "serial.Serial") -> bytes:
    bytes_waiting = getattr(serial_port, "in_waiting", 0)
    read_size = bytes_waiting if bytes_waiting > 0 else 1
    return bytes(serial_port.read(read_size))

  def _send_current_command(self, serial_port: "serial.Serial") -> None:
    with self._command_lock:
      command = ActuatorCommand(self._current_command.servo_angle_degrees,
                                self._current_command.vibration_enabled)
    self._write_command(serial_port, command, log_status="ok")
    self._command_dirty.clear()

  def _write_command(self, serial_port: "serial.Serial", command: ActuatorCommand,
                     log_status: str) -> None:
    frame = self._codec.encode_actuator_command_frame(command, self._tx_sequence)
    written = serial_port.write(frame)
    if written != len(frame):
      raise SerialException(
          f"short write: expected {len(frame)} bytes, wrote {written}")
    self._publish_log("tx", ProtocolCodec.MESSAGE_TYPE_ACTUATOR_COMMAND,
                      self._tx_sequence, ProtocolCodec.bytes_to_hex(frame),
                      log_status)
    self._tx_sequence = (self._tx_sequence + 1) & 0xFF
    self._tx_count += 1
    self._publish_status(True, True, "command sent")

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
            port=self._port,
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

    port = self._port_var.get().strip()
    if not port:
      messagebox.showerror("Missing Port",
                           "Enter a serial port before connecting.")
      return

    self._apply_command_to_widgets(NEUTRAL_SERVO_ANGLE, False)
    self._last_status = None
    self._worker = SerialWorker(port, self._baud_rate, self._keepalive_ms,
                                self._event_queue)
    self._worker.start()
    self._append_log_line("SYSTEM", "--", "--", "",
                          f"connecting to {port} at {self._baud_rate} baud")
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
    self._root.title("Arduino UART Tester")
    self._root.geometry("1100x760")

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

    ttk.Label(top_bar, text="Serial Port").grid(row=0, column=0, padx=(0, 8))
    self._port_combo = ttk.Combobox(top_bar,
                                    textvariable=self._port_var,
                                    state="normal")
    self._port_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(top_bar, text="Refresh",
               command=self.refresh_ports).grid(row=0, column=2, padx=(0, 8))
    self._connect_button = ttk.Button(top_bar,
                                      text="Connect",
                                      command=self.connect)
    self._connect_button.grid(row=0, column=3, padx=(0, 8))
    self._disconnect_button = ttk.Button(top_bar,
                                         text="Disconnect",
                                         command=self.disconnect)
    self._disconnect_button.grid(row=0, column=4, padx=(0, 8))
    ttk.Label(top_bar,
              text=f"Baud: {self._baud_rate}").grid(row=0, column=5, sticky="e")

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
    if list_ports is None:
      return []

    ports = sorted(list_ports.comports(), key=lambda port_info: port_info.device)
    return [port_info.device for port_info in ports]

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
    if event.direction == "rx" and event.status == "ok":
      self._last_rx_var.set(self._format_timestamp(event.timestamp))

  def _handle_worker_status(self, status: WorkerStatus) -> None:
    self._last_status = status
    connection_state = f"{'Connected' if status.connected else 'Disconnected'}"
    keepalive_state = "On" if status.keepalive_active else "Off"
    port_label = status.port if status.port else "--"
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
  parser = argparse.ArgumentParser(description="Tkinter UART tester for the Arduino firmware protocol.")
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
  if SERIAL_IMPORT_ERROR is not None:
    raise SystemExit(f"pyserial is required to run this tool: {SERIAL_IMPORT_ERROR}")

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
