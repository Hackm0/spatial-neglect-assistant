from __future__ import annotations

import arduino_transport


def test_list_connection_options_preserves_gui_order() -> None:
  options = arduino_transport.list_connection_options(
      serial_options_lister=lambda: ("/dev/ttyUSB0", "/dev/ttyUSB1"),
      bluetooth_options_lister=lambda: (
          "Desk Sensor [AA:BB:CC:DD:EE:FF]",
          "HC-05 [98:D3:11:FD:07:FF]",
      ),
  )

  assert options == (
      "/dev/ttyUSB0",
      "/dev/ttyUSB1",
      "Desk Sensor [AA:BB:CC:DD:EE:FF]",
      "HC-05 [98:D3:11:FD:07:FF]",
  )


def test_list_connection_options_deduplicates_preserving_first_occurrence() -> None:
  options = arduino_transport.list_connection_options(
      serial_options_lister=lambda: (
          "/dev/ttyUSB0",
          "HC-05 [98:D3:11:FD:07:FF]",
      ),
      bluetooth_options_lister=lambda: (
          "HC-05 [98:D3:11:FD:07:FF]",
          "Desk Sensor [AA:BB:CC:DD:EE:FF]",
      ),
  )

  assert options == (
      "/dev/ttyUSB0",
      "HC-05 [98:D3:11:FD:07:FF]",
      "Desk Sensor [AA:BB:CC:DD:EE:FF]",
  )
