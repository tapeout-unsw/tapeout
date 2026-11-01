"""Validation and framing for the RTL bootloader's serial protocol."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


MAX_PROGRAM_BYTES = 1024


class ProgramError(ValueError):
    """The supplied image cannot be loaded by the hardware bootloader."""


@dataclass(frozen=True)
class ProgramImage:
    data: bytes
    name: str = "program.bin"

    def __post_init__(self) -> None:
        if not self.data:
            raise ProgramError("program is empty")
        if self.data.startswith(b"\x7fELF"):
            raise ProgramError(
                "this is an ELF executable, not a flat binary; convert it with "
                "riscv*-objcopy -O binary first"
            )
        if len(self.data) > MAX_PROGRAM_BYTES:
            raise ProgramError(
                f"program is {len(self.data)} bytes; the FPGA build allows "
                f"at most {MAX_PROGRAM_BYTES}"
            )
        if len(self.data) % 4:
            raise ProgramError(
                "program length must be a multiple of four bytes; raw binaries "
                "are never silently modified"
            )

    @property
    def word_count(self) -> int:
        return len(self.data) // 4

    @property
    def boot_packet(self) -> bytes:
        return self.boot_packet_with_mask(0)

    def boot_packet_with_mask(self, bypass_mask: int) -> bytes:
        # Header bits 31:28 bypass ic0, ic1, dc0, dc1 respectively.
        if not isinstance(bypass_mask, int) or not 0 <= bypass_mask <= 15:
            raise ValueError("cache bypass mask must be between 0 and 15")
        header = (bypass_mask << 28) | self.word_count
        return struct.pack("<I", header) + self.data

    @classmethod
    def from_path(cls, path: str | Path) -> "ProgramImage":
        source = Path(path)
        return cls(source.read_bytes(), source.name)
