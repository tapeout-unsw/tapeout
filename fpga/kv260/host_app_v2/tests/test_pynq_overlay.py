import tempfile
import unittest
from pathlib import Path

from tapeout_host.pynq_overlay import PynqHardware


class FakeOverlay:
    def __init__(self, bitstream, download):
        self.bitstream = bitstream
        self.download = download
        self.ip_dict = {
            "uart": {"phys_addr": 0xA0000000, "addr_range": 0x10000},
            "control_status": {"phys_addr": 0xA0010000, "addr_range": 0x10000},
        }

    def is_loaded(self):
        return True


class FakeMmio:
    instances = []

    def __init__(self, address, length):
        self.address = address
        self.length = length
        self.values = {}
        self.instances.append(self)

    def read(self, offset, length):
        return self.values.get(offset, 0)

    def write(self, offset, value):
        self.values[offset] = value


class PynqHardwareTest(unittest.TestCase):
    def test_loads_overlay_and_finds_regions_from_hwh(self):
        FakeMmio.instances.clear()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            bitstream = Path(directory) / "system_wrapper.bit"
            bitstream.touch()
            bitstream.with_suffix(".hwh").touch()

            hardware = PynqHardware(
                bitstream,
                overlay_factory=FakeOverlay,
                mmio_factory=FakeMmio,
            )

        self.assertEqual(
            [(item.address, item.length) for item in FakeMmio.instances],
            [(0xA0000000, 0x10000), (0xA0010000, 0x10000)],
        )
        hardware.uart_region.write32(4, 0x1_0000_0041)
        self.assertEqual(hardware.uart_region.read32(4), 0x41)

    def test_requires_same_name_hwh(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            bitstream = Path(directory) / "system_wrapper.bit"
            bitstream.touch()
            with self.assertRaisesRegex(FileNotFoundError, "same base name"):
                PynqHardware(
                    bitstream,
                    overlay_factory=FakeOverlay,
                    mmio_factory=FakeMmio,
                )

    def test_overlay_only_does_not_create_mmio_regions(self):
        FakeMmio.instances.clear()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            bitstream = Path(directory) / "system_wrapper.bit"
            bitstream.touch()
            bitstream.with_suffix(".hwh").touch()
            hardware = PynqHardware(
                bitstream,
                map_regions=False,
                overlay_factory=FakeOverlay,
                mmio_factory=FakeMmio,
            )

        self.assertEqual(FakeMmio.instances, [])
        self.assertIsNone(hardware.uart_region)
        self.assertIsNone(hardware.gpio_region)


if __name__ == "__main__":
    unittest.main()
