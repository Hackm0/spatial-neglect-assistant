# UART Protocol

## Overview
- Link settings: `115200 8N1`
- Little-endian

All sensor output is published as binary telemetry frames.

## Frame Layout

| Byte Offset | Field | Size | Notes |
| --- | --- | --- | --- |
| 0 | Sync 1 | 1 | `0xA5` |
| 1 | Sync 2 | 1 | `0x5A` |
| 2 | Version | 1 | `0x01` |
| 3 | Message Type | 1 | `0x01` command, `0x81` telemetry |
| 4 | Sequence | 1 | Monotonic modulo 256 |
| 5 | Payload Length | 1 | `0..32` |
| 6..N | Payload | `0..32` | Message-specific |
| N+1..N+2 | CRC16 | 2 | CRC-16/CCITT-FALSE over bytes `2..N` |

## CRC
- Variant: `CRC-16/CCITT-FALSE`
- Polynomial: `0x1021`
- Initial value: `0xFFFF`
- Reflection: none
- Final XOR: `0x0000`
- Coverage: `version + message_type + sequence + payload_length + payload`
- Wire order: CRC low byte first, then CRC high byte

## Messages

### `0x01` ActuatorCommand

Payload length: `3`

| Offset | Field | Type | Notes |
| --- | --- | --- | --- |
| 0..1 | `servo_angle_tenths_deg` | `uint16_t` | Servo target in tenths of a degree |
| 2 | `flags` | `uint8_t` | Bit `0`: vibration enabled. Bits `1..7` must be `0` |

Behavior:
- The device clamps the servo angle to the configured servo limits before applying it.
- The host should resend this frame at least every `50 ms`.
- If no valid command arrives for `250 ms`, the device enters fail-safe: servo target returns to its configured neutral angle and vibration turns off.

### `0x81` TelemetrySnapshot

Payload length: `14`

| Offset | Field | Type | Notes |
| --- | --- | --- | --- |
| 0..1 | `distance_mm` | `uint16_t` | `0` when distance is invalid |
| 2..3 | `accel_x_mg` | `int16_t` | `0` when accelerometer is invalid |
| 4..5 | `accel_y_mg` | `int16_t` | `0` when accelerometer is invalid |
| 6..7 | `accel_z_mg` | `int16_t` | `0` when accelerometer is invalid |
| 8..9 | `joystick_x_permille` | `int16_t` | `-1000..1000` |
| 10..11 | `joystick_y_permille` | `int16_t` | `-1000..1000` |
| 12 | `button_flags` | `uint8_t` | Bit `0`: joystick button pressed |
| 13 | `sensor_flags` | `uint8_t` | Bit `0`: distance valid, bit `1`: distance timed out, bit `2`: accelerometer valid |

Telemetry cadence:
- The device sends one telemetry frame every `50 ms`.
- Joystick values always contain the latest sampled position.
- Distance timeout is reported by setting `distance_mm = 0`, `distance valid = 0`, and `distance timed out = 1`.
- If the accelerometer is unavailable, all accel fields are `0` and `accelerometer valid = 0`.

## Worked Examples

### Example: ActuatorCommand

Intent:
- Servo target: `90.0 deg`
- Vibration: enabled
- Sequence: `0x05`

Encoded bytes:

```text
A5 5A 01 01 05 03 84 03 01 6C 16
```

Breakdown:
- `A5 5A`: sync
- `01`: version
- `01`: message type
- `05`: sequence
- `03`: payload length
- `84 03`: `900` tenths of a degree = `90.0 deg`
- `01`: vibration enabled
- `6C 16`: CRC low/high

### Example: TelemetrySnapshot

Intent:
- Distance: `321 mm`
- Accel: `+125 mg`, `-250 mg`, `+1000 mg`
- Joystick: `+500`, `-500`
- Button pressed
- Distance valid, accelerometer valid
- Sequence: `0x00`

Encoded bytes:

```text
A5 5A 01 81 00 0E 41 01 7D 00 06 FF E8 03 F4 01 0C FE 01 05 00 80
```

Breakdown:
- `41 01`: distance `321`
- `7D 00`: accel X `125`
- `06 FF`: accel Y `-250`
- `E8 03`: accel Z `1000`
- `F4 01`: joystick X `500`
- `0C FE`: joystick Y `-500`
- `01`: button pressed
- `05`: distance valid + accelerometer valid
- `00 80`: CRC low/high

## Receiver Notes
- Drop any frame whose CRC is invalid.
- Drop any frame with version other than `0x01`.
- Drop any frame with payload length greater than `32`.
- Drop `ActuatorCommand` frames with reserved flag bits set.
- Unknown message types may be ignored after CRC validation.