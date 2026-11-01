import unittest

from tapeout_host.mmio import ControlStatus, MmioError, UartLite


class FakeRegion:
    def __init__(self):
        self.values = {}
        self.writes = []

    def read32(self, offset):
        return self.values.get(offset, 0)

    def write32(self, offset, value):
        self.values[offset] = value
        self.writes.append((offset, value))


class MmioTests(unittest.TestCase):
    def test_gpio_status_layout_matches_block_design_concat(self):
        region = FakeRegion()
        control = ControlStatus(region)
        region.values[control.DATA2] = 0b10111
        status = control.read()
        self.assertTrue(status.done)
        self.assertTrue(status.core_run)
        self.assertTrue(status.lock)
        self.assertEqual(status.exit_bits, 0b10)

    def test_uart_write_places_each_byte_in_tx_fifo(self):
        region = FakeRegion()
        uart = UartLite(region)
        uart.write(b"ABC")
        values = [value for offset, value in region.writes if offset == uart.TX_FIFO]
        self.assertEqual(values, [65, 66, 67])

    def test_uart_reports_receive_errors(self):
        region = FakeRegion()
        uart = UartLite(region)
        region.values[uart.STATUS] = uart.RX_OVERRUN
        with self.assertRaises(MmioError):
            uart.read_available()

    def test_uart_reports_errors_even_during_transmit(self):
        region = FakeRegion()
        uart = UartLite(region)
        region.values[uart.STATUS] = uart.RX_OVERRUN
        with self.assertRaises(MmioError):
            uart.write(b"x")

    def test_uart_drains_receive_fifo_while_transmitting(self):
        class ReceivingRegion(FakeRegion):
            def __init__(self):
                super().__init__()
                self.pending = [65, 66]

            def read32(self, offset):
                if offset == UartLite.STATUS:
                    return UartLite.RX_VALID if self.pending else 0
                if offset == UartLite.RX_FIFO:
                    return self.pending.pop(0)
                return 0

        region = ReceivingRegion()
        received = []
        UartLite(region).write(b"xy", on_receive=received.append)
        self.assertEqual(b"".join(received), b"AB")
        self.assertEqual([value for _, value in region.writes], [120, 121])


if __name__ == "__main__":
    unittest.main()
