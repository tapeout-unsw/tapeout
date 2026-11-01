import itertools
import unittest
from pathlib import Path
from unittest.mock import patch

from tapeout_host.board import TapeoutBoard
from tapeout_host.mmio import SocStatus
from tapeout_host.protocol import ProgramImage


class Hardware:
    uart_region = None
    gpio_region = None

    def __init__(self, path):
        pass


class Uart:
    def reset_fifos(self):
        pass

    def write(self, packet, timeout, on_receive):
        pass

    def read_available(self):
        return b""


class Control:
    def __init__(self, statuses):
        self.statuses = iter(statuses)

    def pulse_reset(self):
        pass

    def read(self):
        return next(self.statuses)


class ExecutionTimingTests(unittest.TestCase):
    def run_with_statuses(self, statuses):
        with patch("tapeout_host.board.ControlStatus"), patch("tapeout_host.board.UartLite"):
            board = TapeoutBoard(Path("unused"), hardware_factory=Hardware)
        board.uart = Uart()
        board.control = Control(statuses)
        clock = itertools.count()
        with patch("tapeout_host.board.time.monotonic", side_effect=lambda: next(clock)), \
             patch("tapeout_host.board.time.sleep"):
            return board.run(ProgramImage(b"\x13\0\0\0"), 10)

    def test_reports_interval_between_running_and_done_samples(self):
        running = SocStatus(done=False, core_run=True, lock=False, exit_bits=0)
        done = SocStatus(done=True, core_run=True, lock=False, exit_bits=3)
        result = self.run_with_statuses([running, done, done])
        self.assertEqual(result.observed_execution_seconds, 2)

    def test_first_sample_already_done_has_no_timing(self):
        done = SocStatus(done=True, core_run=True, lock=False, exit_bits=3)
        result = self.run_with_statuses([done, done])
        self.assertIsNone(result.observed_execution_seconds)


if __name__ == "__main__":
    unittest.main()
