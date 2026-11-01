"""High-level load/run operation for the FPGA-resident processor."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .mmio import ControlStatus, SocStatus, UartLite
from .pynq_overlay import PynqHardware
from .protocol import ProgramImage


EventCallback = Callable[[str, dict], None]

# At 115200 baud a 16-byte UARTLite FIFO fills in roughly 1.4 ms. Poll much
# faster than that while a program is running or finishing its final output.
UART_POLL_SECONDS = 0.0002


@dataclass(frozen=True)
class RunResult:
    completed: bool
    timed_out: bool
    elapsed_seconds: float
    uart: bytes
    status: SocStatus


class TapeoutBoard:
    def __init__(
        self,
        bitstream: Path,
        *,
        hardware_factory=PynqHardware,
    ):
        self._hardware = hardware_factory(bitstream)
        self._uart_region = self._hardware.uart_region
        self._gpio_region = self._hardware.gpio_region
        self.uart = UartLite(self._uart_region)
        self.control = ControlStatus(self._gpio_region)
        self._run_lock = threading.Lock()

    def close(self) -> None:
        self._hardware.close()

    def run(
        self,
        image: ProgramImage,
        timeout: float,
        emit: EventCallback = lambda _kind, _data: None,
    ) -> RunResult:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("another program is already running")

        started = time.monotonic()
        received = bytearray()
        last_status: SocStatus | None = None
        def record_uart(chunk: bytes) -> None:
            received.extend(chunk)
            emit("uart", {"text": chunk.decode("utf-8", errors="replace")})

        try:
            emit("log", {"message": "Resetting UART FIFOs and processor"})
            self.uart.reset_fifos()
            self.control.pulse_reset()

            emit(
                "log",
                {
                    "message": (
                        f"Loading {image.name}: {len(image.data)} bytes, "
                        f"{image.word_count} words"
                    )
                },
            )
            self.uart.write(image.boot_packet, timeout=max(3.0, timeout), on_receive=record_uart)

            deadline = time.monotonic() + timeout
            timed_out = False
            while True:
                chunk = self.uart.read_available()
                if chunk:
                    record_uart(chunk)

                status = self.control.read()
                if status != last_status:
                    emit("status", status.as_dict())
                    last_status = status
                if status.done:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(UART_POLL_SECONDS)

            # DONE can rise while the final UART byte is still shifting. Wait
            # briefly for a quiet receive FIFO so the console is not truncated.
            quiet_deadline = time.monotonic() + 0.05
            while time.monotonic() < quiet_deadline:
                chunk = self.uart.read_available()
                if chunk:
                    record_uart(chunk)
                    quiet_deadline = time.monotonic() + 0.05
                time.sleep(UART_POLL_SECONDS)

            status = self.control.read()
            return RunResult(
                completed=status.done,
                timed_out=timed_out,
                elapsed_seconds=time.monotonic() - started,
                uart=bytes(received),
                status=status,
            )
        finally:
            self._run_lock.release()
