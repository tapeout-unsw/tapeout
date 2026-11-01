"""Load the Vivado design with PYNQ and expose its AXI register regions."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class PynqRegion:
    """Adapt ``pynq.MMIO`` to the register interface used by the drivers."""

    def __init__(self, mmio):
        self._mmio = mmio
        self._lock = threading.Lock()

    def read32(self, offset: int) -> int:
        with self._lock:
            return int(self._mmio.read(offset, 4))

    def write32(self, offset: int, value: int) -> None:
        with self._lock:
            self._mmio.write(offset, value & 0xFFFF_FFFF)

    def close(self) -> None:
        # PYNQ owns the underlying mapping for the lifetime of the process.
        pass


class PynqHardware:
    """Program the PL and locate the UART/GPIO from the matching HWH file."""

    UART_INSTANCE = "uart"
    GPIO_INSTANCE = "control_status"

    def __init__(
        self,
        bitstream: Path,
        *,
        map_regions: bool = True,
        overlay_factory: Callable | None = None,
        mmio_factory: Callable | None = None,
    ):
        bitstream = bitstream.expanduser().resolve()
        handoff = bitstream.with_suffix(".hwh")
        if not bitstream.is_file():
            raise FileNotFoundError(f"PYNQ bitstream not found: {bitstream}")
        if not handoff.is_file():
            raise FileNotFoundError(
                f"PYNQ hardware handoff not found: {handoff}. The .bit and .hwh "
                "files must have the same base name and be in the same directory."
            )

        if overlay_factory is None or mmio_factory is None:
            try:
                from pynq import MMIO, Overlay
            except ImportError as exc:
                raise RuntimeError(
                    "PYNQ is not available in this Python installation. Run the "
                    "host app with the board's PYNQ Python environment."
                ) from exc
            overlay_factory = overlay_factory or Overlay
            mmio_factory = mmio_factory or MMIO

        # Overlay parses the HWH first, sets the PS-to-PL clock configuration,
        # and then programs the bitstream. This is the part raw fpgautil lacked.
        self.overlay = overlay_factory(str(bitstream), download=True)
        if hasattr(self.overlay, "is_loaded") and not self.overlay.is_loaded():
            raise RuntimeError("PYNQ did not report the overlay as loaded")

        self.uart_region = None
        self.gpio_region = None
        if map_regions:
            self.uart_region = self._open_region(
                self.UART_INSTANCE, self.overlay.ip_dict, mmio_factory
            )
            self.gpio_region = self._open_region(
                self.GPIO_INSTANCE, self.overlay.ip_dict, mmio_factory
            )

    @staticmethod
    def _open_region(name: str, ip_dict: dict, mmio_factory: Callable) -> PynqRegion:
        try:
            description = ip_dict[name]
        except KeyError as exc:
            available = ", ".join(sorted(ip_dict)) or "none"
            raise RuntimeError(
                f"HWH does not contain AXI instance {name!r}; available: {available}"
            ) from exc

        try:
            address = int(description["phys_addr"])
            length = int(description["addr_range"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid PYNQ description for AXI instance {name!r}") from exc
        return PynqRegion(mmio_factory(address, length))

    def close(self) -> None:
        if self.gpio_region is not None:
            self.gpio_region.close()
        if self.uart_region is not None:
            self.uart_region.close()
