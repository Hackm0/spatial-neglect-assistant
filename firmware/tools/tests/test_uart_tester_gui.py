import sys
import unittest
from pathlib import Path
from unittest import mock


TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
  sys.path.insert(0, str(TOOLS_ROOT))

import uart_tester_gui


class BuildConnectionConfigTest(unittest.TestCase):
  def test_port_is_normalized(self) -> None:
    connection = uart_tester_gui.build_connection_config(" /dev/rfcomm0 ")

    self.assertEqual("/dev/rfcomm0", connection.port)
    self.assertEqual("/dev/rfcomm0", connection.describe())

  def test_rfcomm_name_without_dev_prefix_is_normalized(self) -> None:
    connection = uart_tester_gui.build_connection_config(" rfcomm0 ")

    self.assertEqual("/dev/rfcomm0", connection.port)

  def test_bluetooth_mac_address_creates_bluetooth_connection(self) -> None:
    connection = uart_tester_gui.build_connection_config(
        "98:d3:11:fd:07:ff")

    self.assertIsInstance(connection, uart_tester_gui.BluetoothConnectionConfig)
    self.assertEqual("98:D3:11:FD:07:FF", connection.address)
    self.assertEqual(uart_tester_gui.DEFAULT_BLUETOOTH_RFCOMM_CHANNEL,
                     connection.channel)
    self.assertEqual("Bluetooth 98:D3:11:FD:07:FF", connection.describe())

  def test_bluetooth_device_label_extracts_name_and_address(self) -> None:
    connection = uart_tester_gui.build_connection_config(
        "HC-05 [98:D3:11:FD:07:FF]")

    self.assertIsInstance(connection, uart_tester_gui.BluetoothConnectionConfig)
    self.assertEqual("98:D3:11:FD:07:FF", connection.address)
    self.assertEqual("HC-05", connection.device_name)
    self.assertEqual("HC-05 [98:D3:11:FD:07:FF]", connection.describe())

  def test_bluetooth_uri_extracts_channel(self) -> None:
    connection = uart_tester_gui.build_connection_config(
        "bluetooth://98:D3:11:FD:07:FF/3")

    self.assertIsInstance(connection, uart_tester_gui.BluetoothConnectionConfig)
    self.assertEqual("98:D3:11:FD:07:FF", connection.address)
    self.assertEqual(3, connection.channel)

  def test_missing_port_is_rejected(self) -> None:
    with self.assertRaisesRegex(ValueError, "port"):
      uart_tester_gui.build_connection_config("  ")

  def test_resolve_rfcomm_connection_uses_direct_bluetooth_socket(self) -> None:
    connection = uart_tester_gui.SerialConnectionConfig("/dev/rfcomm0")
    binding = uart_tester_gui.RfcommBinding(
        port="/dev/rfcomm0",
        address="98:D3:11:FD:07:FF",
        channel=1,
        state="connected [tty-attached]",
    )

    with mock.patch.object(uart_tester_gui,
                           "lookup_rfcomm_binding",
                           return_value=binding):
      resolved = uart_tester_gui.resolve_connection_config(connection)

    self.assertIsInstance(resolved, uart_tester_gui.BluetoothConnectionConfig)
    self.assertEqual("98:D3:11:FD:07:FF", resolved.address)
    self.assertEqual(1, resolved.channel)


class ParseBaudRateTest(unittest.TestCase):
  def test_valid_baud_rate_is_parsed(self) -> None:
    self.assertEqual(38400, uart_tester_gui.parse_baud_rate(" 38400 "))

  def test_missing_baud_rate_is_rejected(self) -> None:
    with self.assertRaisesRegex(ValueError, "baud rate"):
      uart_tester_gui.parse_baud_rate(" ")

  def test_non_numeric_baud_rate_is_rejected(self) -> None:
    with self.assertRaisesRegex(ValueError, "integer baud rate"):
      uart_tester_gui.parse_baud_rate("fast")


class RfcommHelperTest(unittest.TestCase):
  def test_is_rfcomm_port_detects_linux_rfcomm_device(self) -> None:
    self.assertTrue(uart_tester_gui.is_rfcomm_port("/dev/rfcomm0"))
    self.assertFalse(uart_tester_gui.is_rfcomm_port("/dev/ttyACM0"))

  def test_connection_reset_settle_seconds_skips_rfcomm_wait(self) -> None:
    self.assertEqual(
        0.0,
        uart_tester_gui.connection_reset_settle_seconds("/dev/rfcomm0"),
    )
    self.assertGreater(
        uart_tester_gui.connection_reset_settle_seconds("/dev/ttyACM0"),
        0.0,
    )

  def test_parse_rfcomm_bindings_extracts_binding_details(self) -> None:
    bindings = uart_tester_gui.parse_rfcomm_bindings(
        "rfcomm0: 98:D3:11:FD:07:FF channel 1 connected [tty-attached]\n"
        "rfcomm1: aa:bb:cc:dd:ee:ff channel 3 closed \n")

    self.assertEqual(2, len(bindings))
    self.assertEqual("/dev/rfcomm0", bindings["/dev/rfcomm0"].port)
    self.assertEqual("98:D3:11:FD:07:FF",
                     bindings["/dev/rfcomm0"].address)
    self.assertEqual(1, bindings["/dev/rfcomm0"].channel)
    self.assertEqual("connected [tty-attached]",
                     bindings["/dev/rfcomm0"].state)
    self.assertEqual("AA:BB:CC:DD:EE:FF",
                     bindings["/dev/rfcomm1"].address)
    self.assertEqual("closed", bindings["/dev/rfcomm1"].state)

  def test_parse_bluetooth_connected_extracts_connection_state(self) -> None:
    self.assertTrue(
        uart_tester_gui.parse_bluetooth_connected(
            "\x1b[0;94m[HC-05]\x1b[0m# info 98:D3:11:FD:07:FF\n"
            "Connected: yes\n"))
    self.assertFalse(
        uart_tester_gui.parse_bluetooth_connected(
            "[bluetooth]# info 98:D3:11:FD:07:FF\nConnected: no\n"))
    self.assertIsNone(
        uart_tester_gui.parse_bluetooth_connected("info unavailable"))

  def test_parse_bluetooth_devices_extracts_names_and_addresses(self) -> None:
    devices = uart_tester_gui.parse_bluetooth_devices(
        "\x1b[0;94mDevice 98:D3:11:FD:07:FF HC-05\x1b[0m\n"
        "Device aa:bb:cc:dd:ee:ff Desk Sensor\n")

    self.assertEqual(2, len(devices))
    self.assertEqual("HC-05", devices["98:D3:11:FD:07:FF"].name)
    self.assertEqual("Desk Sensor", devices["AA:BB:CC:DD:EE:FF"].name)


if __name__ == "__main__":
  unittest.main()
