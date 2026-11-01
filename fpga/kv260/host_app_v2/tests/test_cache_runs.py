import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tapeout_host.runs import RunManager


class Upload:
    filename = "test.bin"

    def save(self, path):
        Path(path).write_bytes(b"\x13\0\0\0")


class Board:
    def __init__(self):
        self.mask = None

    def run(self, image, timeout, emit, bypass_mask):
        self.mask = bypass_mask
        return SimpleNamespace(
            completed=True, timed_out=False, elapsed_seconds=0.1,
            observed_execution_seconds=0.02,
            status=SimpleNamespace(as_dict=lambda: {"done": True}), uart=b"PASS\n",
        )


class CacheRunTests(unittest.TestCase):
    def test_mask_reaches_board_and_is_saved_with_result(self):
        board = Board()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            record = RunManager(board, Path(directory)).create("binary", Upload(), 5, 10)
            deadline = time.monotonic() + 5
            while record.state not in {"passed", "failed"} and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(record.state, "passed")
            self.assertEqual(board.mask, 10)
            self.assertEqual(record.result["bypass_mask"], 10)
            self.assertIn('"bypass_mask": 10', (record.directory / "result.json").read_text())

    def test_bad_mask_rejected_before_creating_run(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            with self.assertRaises(ValueError):
                RunManager(Board(), Path(directory)).create("binary", Upload(), 5, 16)


if __name__ == "__main__":
    unittest.main()
