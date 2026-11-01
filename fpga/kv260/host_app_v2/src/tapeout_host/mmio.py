"""Register-level drivers for the two AXI peripherals in the block design."""

from __future__ import annotations

import mmap
import os
import struct
import threading
import time
from dataclasses import dataclass
from typing import Protocol


UART_BASE = 0xA000_0000
GPIO_BASE = 0xA001_0000
AXI_RANGE = 0x1_0000


class MmioError(RuntimeError):
    pass


class DevMemRegion:
    """Page-aligned 32-bit MMIO mapping backed by Linux /dev/mem."""

    def __init__(self, address: int, length: int = AXI_RANGE, device: str = "/dev/mem"):
        page_size = mmap.PAGESIZE
        page_base = address & ~(page_size - 1)
        self._offset = address - page_base
        map_length = self._offset + length
        try:
            self._fd = os.open(device, os.O_RDWR | os.O_SYNC)
            self._map = mmap.mmap(
                self._fd,
                map_length,
                flags=mmap.MAP_SHARED,
                prot=mmap.PROT_READ | mmap.PROT_WRITE,
                offset=page_base,
            )
        except OSError as exc:
            raise MmioError(
                f"cannot map {device} at 0x{address:08x}; run the service as "
                "root and make sure the FPGA bitstream is loaded"
            ) from exc
        self._lock = threading.Lock()

    def read32(self, offset: int) -> int:
        with self._lock:
            return struct.unpack_from("<I", self._map, self._offset + offset)[0]

    def write32(self, offset: int, value: int) -> None:
        with self._lock:
            struct.pack_into("<I", self._map, self._offset + offset, value & 0xFFFF_FFFF)

    def close(self) -> None:
        self._map.close()
        os.close(self._fd)


class RegisterRegion(Protocol):
    """The small MMIO interface used by the device drivers below."""

    def read32(self, offset: int) -> int: ...

    def write32(self, offset: int, value: int) -> None: ...

    def close(self) -> None: ...


class UartLite:
    RX_FIFO = 0x00
    TX_FIFO = 0x04
    STATUS = 0x08
    CONTROL = 0x0C

    RX_VALID = 0x01
    TX_FULL = 0x08
    RX_OVERRUN = 0x20
    RX_FRAME_ERROR = 0x40
    RX_PARITY_ERROR = 0x80

    RESET_TX = 0x01
    RESET_RX = 0x02

    def __init__(self, region: RegisterRegion):
        self.region = region

    def reset_fifos(self) -> None:
        self.region.write32(self.CONTROL, self.RESET_TX | self.RESET_RX)
        self.region.write32(self.CONTROL, 0)

    def _checked_status(self) -> int:
        # UARTLite clears receive error flags whenever STATUS is read, even
        # when the read was intended only to check the transmit FIFO.
        status = self.region.read32(self.STATUS)
        if status & (self.RX_OVERRUN | self.RX_FRAME_ERROR | self.RX_PARITY_ERROR):
            raise MmioError(f"AXI UARTLite receive error, status=0x{status:02x}")
        return status

    def write(self, payload: bytes, timeout: float = 3.0, on_receive=None) -> None:
        deadline = time.monotonic() + timeout
        for value in payload:
            while True:
                status = self._checked_status()
                if status & self.RX_VALID:
                    chunk = bytes([self.region.read32(self.RX_FIFO) & 0xFF])
                    if on_receive is not None:
                        on_receive(chunk)
                    else:
                        raise MmioError("unexpected UART data while transmitting")
                    continue
                if not status & self.TX_FULL:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("AXI UARTLite transmit FIFO stayed full")
                time.sleep(0.0002)
            self.region.write32(self.TX_FIFO, value)

    def read_available(self) -> bytes:
        result = bytearray()
        while True:
            status = self._checked_status()
            if not status & self.RX_VALID:
                break
            result.append(self.region.read32(self.RX_FIFO) & 0xFF)
        return bytes(result)


@dataclass(frozen=True)
class SocStatus:
    done: bool
    core_run: bool
    lock: bool
    exit_bits: int

    def as_dict(self) -> dict[str, bool | int]:
        return {
            "done": self.done,
            "core_run": self.core_run,
            "lock": self.lock,
            "exit_bits": self.exit_bits,
        }


class ControlStatus:
    DATA = 0x00
    TRI = 0x04
    DATA2 = 0x08
    TRI2 = 0x0C

    def __init__(self, region: RegisterRegion):
        self.region = region
        # GPIO channel 1 drives reset; channel 2 is the five-bit status input.
        self.region.write32(self.TRI, 0x0)
        self.region.write32(self.TRI2, 0x1F)

    def set_reset(self, asserted: bool) -> None:
        self.region.write32(self.DATA, 1 if asserted else 0)

    def pulse_reset(self, seconds: float = 0.01) -> None:
        self.set_reset(True)
        time.sleep(seconds)
        self.set_reset(False)

    def read(self) -> SocStatus:
        raw = self.region.read32(self.DATA2) & 0x1F
        return SocStatus(
            done=bool(raw & 0x01),
            core_run=bool(raw & 0x02),
            lock=bool(raw & 0x04),
            exit_bits=(raw >> 3) & 0x03,
        )
