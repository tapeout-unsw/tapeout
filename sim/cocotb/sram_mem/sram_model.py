"""Shared helpers for the sram_mem level-1 (unit) tests.

The reference memory is written from the interface description in
rtl/mem/sram_mem.sv, not derived from the RTL, so the tests check the RTL
against its specification rather than against itself.

Timing convention used by every test:
  * inputs change on the FALLING edge of clk,
  * the SRAM samples them on the next RISING edge,
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


class ByteMemory:
    """Reference memory, one entry per byte. None means never written, so the
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


def run_cocotb(*, words: int, macro: bool, test_module: str) -> None:
    """Build sram_mem (with its four sram_macro lanes) and run one cocotb
    test module. Called from pytest.

    words  words per lane: 256, 512 or 1024
    macro  False: sram_macro's behavioural memory
           True:  the OCD macro models in ip/gf180mcu_ocd_ip_sram/

    Environment variables:
      SIM=verilator|icarus  simulator (default verilator)
      WAVES=1               dump waveforms into the build directory
      SEED=<n>              reproduce a random run
    """
    from cocotb_tools.runner import get_runner

    sim = os.environ.get("SIM", "verilator")
    waves = os.environ.get("WAVES", "0") == "1"

    sources = [ROOT / "rtl/mem/sram_macro.sv", ROOT / "rtl/mem/sram_mem.sv"]
    defines: dict = {}
    build_args: list[str] = []
    if macro:
        # the three simulation models, not the __blackbox.v files
        sources += sorted((ROOT / "ip/gf180mcu_ocd_ip_sram").glob("*x8m8wm1.v"))
        defines["USE_SRAM_MACRO"] = 1
    if sim == "verilator":
        build_args.append("-Wno-fatal")          # lint is make lint's job
        if macro:
            build_args += ["--timing", "-Wno-SPECIFYIGN"]

    model = "macro" if macro else "behav"
    build_dir = ROOT / "sim" / "obj_dir" / f"cocotb_sram_mem_w{words}_{model}"
    runner = get_runner(sim)
    runner.build(sources=sources, hdl_toplevel="sram_mem",
                 parameters={"WORDS": words}, defines=defines,
                 build_args=build_args, build_dir=build_dir, always=True, waves=waves)
    runner.test(test_module=test_module, hdl_toplevel="sram_mem",
                build_dir=build_dir, test_dir=build_dir,
                extra_env={"SRAM_WORDS": str(words)},
                seed=os.environ.get("SEED"), waves=waves)
