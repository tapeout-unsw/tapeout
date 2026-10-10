"""Reference models and helpers for the parked level-2 SRAM tests (see README.md).
A copy of the original sram_model.py; merge into a common module at level 2.

Nothing here is derived from the RTL. The byte memory and the RV32 load/store
rules come from the ISA, so the tests check the RTL against the specification
rather than against itself.

Timing convention used by every test:
  * inputs change on the FALLING edge of clk,
  * the RTL samples them on the next RISING edge,
  * outputs are read in the ReadOnly phase straight after that rising edge,
    when every register and combinational path has settled.
"""

from __future__ import annotations

import os
from pathlib import Path

from cocotb.clock import Clock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]                       # repository root
CLK_NS = 20                                  # 50 MHz, the planned chip clock
MASK32 = 0xFFFF_FFFF

# RV32 func3 encodings (rtl/core/mem_access.sv uses the same values)
LB, LH, LW, LBU, LHU = 0b000, 0b001, 0b010, 0b100, 0b101
SB, SH, SW = 0b000, 0b001, 0b010


# ----------------------------------------------------------------- helpers

def start_clock(clk) -> None:
    """Start a 50 MHz clock. cocotb stops it automatically when the test ends."""
    Clock(clk, CLK_NS, unit="ns").start()


def as_int(handle) -> int:
    """A signal's value as an int. Fails the test if any bit is X or Z."""
    value = handle.value
    try:
        return int(value)
    except ValueError as err:
        raise AssertionError(f"{handle._path} is not fully 0/1: {value}") from err


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# -------------------------------------------------------- reference memory

class ByteMemory:
    """One entry per byte. None means the byte has never been written, so its
    contents are undefined (real SRAM powers up random)."""

    def __init__(self, nbytes: int) -> None:
        self.bytes: list[int | None] = [None] * nbytes

    def write_word(self, word: int, data: int, strb: int) -> None:
        """Apply a strobed write: lane i is written only if strb bit i is set."""
        for lane in range(4):
            if (strb >> lane) & 1:
                self.bytes[4 * word + lane] = (data >> (8 * lane)) & 0xFF

    def read_word(self, word: int) -> int | None:
        """The 32-bit word, or None if any of its bytes is still undefined."""
        lanes = self.bytes[4 * word: 4 * word + 4]
        if any(b is None for b in lanes):
            return None
        return sum(b << (8 * i) for i, b in enumerate(lanes))


# ------------------------------------------------ RV32 load/store semantics

def store_lanes(func3: int, byte_addr: int, rs2: int) -> tuple[int, int, int]:
    """(word address, data, strobe) an ALIGNED RV32 store must write.
    Only the strobed lanes of data are meaningful."""
    word, off = byte_addr >> 2, byte_addr & 3
    if func3 == SB:
        return word, (rs2 & 0xFF) << (8 * off), 0b1 << off
    if func3 == SH:
        assert off % 2 == 0, f"misaligned sh at {byte_addr:#x}"
        return word, (rs2 & 0xFFFF) << (8 * off), 0b11 << off
    if func3 == SW:
        assert off == 0, f"misaligned sw at {byte_addr:#x}"
        return word, rs2 & MASK32, 0b1111
    raise ValueError(f"not a store func3: {func3:#05b}")


def load_value(func3: int, byte_addr: int, word_value: int) -> int:
    """What an ALIGNED RV32 load returns in rd, given the whole memory word."""
    off = byte_addr & 3
    if func3 in (LB, LBU):
        b = (word_value >> (8 * off)) & 0xFF
        return ((b - 0x100) & MASK32) if (func3 == LB and b & 0x80) else b
    if func3 in (LH, LHU):
        assert off % 2 == 0, f"misaligned lh at {byte_addr:#x}"
        h = (word_value >> (8 * off)) & 0xFFFF
        return ((h - 0x1_0000) & MASK32) if (func3 == LH and h & 0x8000) else h
    if func3 == LW:
        assert off == 0, f"misaligned lw at {byte_addr:#x}"
        return word_value
    raise ValueError(f"not a load func3: {func3:#05b}")


# ------------------------------------------------------------------ runner

def run_cocotb(*, toplevel: str, sources: list[Path], parameters: dict,
               test_module: str, tag: str, extra_env: dict | None = None) -> None:
    """Build the harness and run one cocotb test module (called from pytest).

    Environment variables:
      SIM=verilator|icarus  simulator (default verilator)
      SRAM_MACRO=1          use the OCD macro models instead of the
                            behavioural memory inside sram_macro
      WAVES=1               dump waveforms into the build directory
      SEED=<n>              reproduce a random run
    """
    from cocotb_tools.runner import get_runner

    sim = os.environ.get("SIM", "verilator")
    macro = os.environ.get("SRAM_MACRO", "0") == "1"
    waves = os.environ.get("WAVES", "0") == "1"

    srcs = [ROOT / "rtl/mem/sram_macro.sv", ROOT / "rtl/mem/sram_mem.sv", *sources]
    defines: dict = {}
    build_args: list[str] = []
    if macro:
        # the three simulation models, not the __blackbox.v files
        srcs += sorted((ROOT / "ip/gf180mcu_ocd_ip_sram").glob("*x8m8wm1.v"))
        defines["USE_SRAM_MACRO"] = 1
    if sim == "verilator":
        build_args.append("-Wno-fatal")          # lint is make lint's job
        if macro:
            build_args += ["--timing", "-Wno-SPECIFYIGN"]

    build_dir = ROOT / "sim" / "obj_dir" / f"cocotb_{tag}{'_macro' if macro else ''}"
    runner = get_runner(sim)
    runner.build(sources=srcs, hdl_toplevel=toplevel, parameters=parameters,
                 defines=defines, build_args=build_args, build_dir=build_dir,
                 always=True, waves=waves)
    runner.test(test_module=test_module, hdl_toplevel=toplevel,
                build_dir=build_dir, test_dir=build_dir,
                extra_env=extra_env or {}, seed=os.environ.get("SEED"),
                waves=waves)
