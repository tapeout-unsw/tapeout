"""One core's loads and stores through mem_access and sram_mem.

DUT: sram_core_harness.sv (the core's mem_access in front of sram_mem, no bus).
The test plays the core FSM: a store is S_MEM (one cycle); a load is S_MEM
then S_MEM_W, with the result read during S_MEM_W.

    C1 store_widths_and_offsets   sb at every byte, sh at both halves, sw:
                                  right strobe, right lanes, nothing else changed
    C2 load_extension             lb/lbu/lh/lhu/lw at every legal offset, with
                                  sign bit set and clear
    C3 store_then_load_next_cycle a load straight after a store sees it
    C4 first_and_last_byte        the very first and very last byte of the SRAM
    C5 random_program             random aligned loads and stores vs the RV32 model

Misaligned halfword and word accesses are not tested: the core does not
support them, and compiled code keeps accesses aligned.
"""

from __future__ import annotations

import random

import cocotb
import pytest
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from ref_model import (HERE, LB, LBU, LH, LHU, LW, MASK32, ROOT, SB, SH, SW, ByteMemory,
                        as_int, env_int, load_value, run_cocotb, start_clock, store_lanes)

LOAD_NAMES = {LB: "lb", LH: "lh", LW: "lw", LBU: "lbu", LHU: "lhu"}
STORE_NAMES = {SB: "sb", SH: "sh", SW: "sw"}


class CoreMemPort:
    """Plays core.sv's memory states against the harness."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.words = env_int("SRAM_WORDS", 512)
        self.ref = ByteMemory(self.words * 4)

    async def start(self) -> None:
        for name in ("en", "mem_write", "func3", "byte_addr", "store_data"):
            getattr(self.dut, name).value = 0
        start_clock(self.dut.clk)
        await ClockCycles(self.dut.clk, 8)          # CEN high before the first access

    async def store(self, func3: int, byte_addr: int, rs2: int) -> None:
        """S_MEM for a store: request this cycle, stored at the rising edge.
        Also checks the strobe and lane data mem_access produced."""
        await FallingEdge(self.dut.clk)
        self.dut.en.value = 1
        self.dut.mem_write.value = 1
        self.dut.func3.value = func3
        self.dut.byte_addr.value = byte_addr
        self.dut.store_data.value = rs2 & MASK32
        await ReadOnly()
        word, data, strb = store_lanes(func3, byte_addr, rs2)
        got_strb = as_int(self.dut.wstrb)
        assert got_strb == strb, (f"{STORE_NAMES[func3]} @{byte_addr:#x}: wstrb "
                                  f"{got_strb:04b}, want {strb:04b}")
        got_data = as_int(self.dut.wdata)
        for lane in range(4):
            if (strb >> lane) & 1:
                g, w = (got_data >> 8 * lane) & 0xFF, (data >> 8 * lane) & 0xFF
                assert g == w, f"{STORE_NAMES[func3]} @{byte_addr:#x}: lane {lane} {g:#04x}, want {w:#04x}"
        await RisingEdge(self.dut.clk)
        self.ref.write_word(word, data, strb)

    async def load(self, func3: int, byte_addr: int) -> int:
        """S_MEM then S_MEM_W for a load. func3 and the address stay put, as
        IR and the ALU hold them; en drops in S_MEM_W."""
        await FallingEdge(self.dut.clk)              # S_MEM
        self.dut.en.value = 1
        self.dut.mem_write.value = 0
        self.dut.func3.value = func3
        self.dut.byte_addr.value = byte_addr
        self.dut.store_data.value = random.getrandbits(32)   # must be ignored
        await RisingEdge(self.dut.clk)               # SRAM samples the read
        await FallingEdge(self.dut.clk)              # S_MEM_W
        self.dut.en.value = 0
        await ReadOnly()
        return as_int(self.dut.load_data)

    async def check_load(self, func3: int, byte_addr: int) -> int:
        got = await self.load(func3, byte_addr)
        word = self.ref.read_word(byte_addr >> 2)
        assert word is not None, f"test bug: word {byte_addr >> 2:#x} never written"
        want = load_value(func3, byte_addr, word)
        assert got == want, (f"{LOAD_NAMES[func3]} @{byte_addr:#x}: got {got:#010x}, "
                             f"want {want:#010x} (word {word:#010x})")
        return got


async def setup(dut) -> CoreMemPort:
    port = CoreMemPort(dut)
    await port.start()
    return port


def store_aligned(func3: int, byte_addr: int) -> int:
    """Round a byte address down to the store's natural alignment."""
    size = {SB: 1, SH: 2, SW: 4}[func3]
    return byte_addr & ~(size - 1)


# ------------------------------------------------------------------- tests

@cocotb.test()
async def store_widths_and_offsets(dut):
    """C1: every store width at every legal offset changes exactly its bytes."""
    port = await setup(dut)
    base = 0x40 % (port.words * 4)
    cases = [(SB, off) for off in range(4)] + [(SH, 0), (SH, 2), (SW, 0)]
    for func3, off in cases:
        await port.store(SW, base, 0xA5A5_A5A5)          # known background
        await port.store(func3, base + off, 0x8765_4321)
        await port.check_load(LW, base)                  # whole word: only the stored bytes changed


@cocotb.test()
async def load_extension(dut):
    """C2: every load width at every legal offset, sign bit set and clear."""
    port = await setup(dut)
    base = 0x80 % (port.words * 4)
    for word in (0x80FF_7F01, 0x7F01_80FF, 0xFFFF_FFFF, 0x0000_0000):
        await port.store(SW, base, word)
        for off in range(4):
            await port.check_load(LB, base + off)
            await port.check_load(LBU, base + off)
        for off in (0, 2):
            await port.check_load(LH, base + off)
            await port.check_load(LHU, base + off)
        await port.check_load(LW, base)


@cocotb.test()
async def store_then_load_next_cycle(dut):
    """C3: a load in the cycle straight after a store sees the new value."""
    port = await setup(dut)
    base = 0xC0 % (port.words * 4)
    await port.store(SW, base, 0)
    for func3, off, value, load in ((SB, 1, 0x9A, LBU), (SH, 2, 0xBEEF, LHU), (SW, 0, 0x1234_5678, LW)):
        await port.store(func3, base + off, value)
        await port.check_load(load, base + off)


@cocotb.test()
async def first_and_last_byte(dut):
    """C4: the first and last byte, halfword and word of the SRAM."""
    port = await setup(dut)
    last = port.words * 4 - 1
    await port.store(SW, 0, 0)
    await port.store(SW, last - 3, 0)
    await port.store(SB, 0, 0x11)
    await port.store(SB, last, 0xEE)
    await port.check_load(LBU, 0)
    await port.check_load(LB, last)
    await port.store(SH, last - 1, 0xC0DE)
    await port.check_load(LHU, last - 1)
    await port.check_load(LW, last - 3)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def random_program(dut):
    """C5: a random mix of aligned loads and stores, checked against the RV32
    model. Reproduce with SEED=<n>."""
    port = await setup(dut)
    nbytes = port.words * 4
    for w in range(port.words):                       # define every byte first
        await port.store(SW, 4 * w, random.getrandbits(32))
    for _ in range(3000):
        addr = random.randrange(nbytes)
        if random.random() < 0.5:
            func3 = random.choice((SB, SH, SW))
            await port.store(func3, store_aligned(func3, addr), random.getrandbits(32))
        else:
            func3 = random.choice((LB, LBU, LH, LHU, LW))
            size = 1 if func3 in (LB, LBU) else 2 if func3 in (LH, LHU) else 4
            await port.check_load(func3, addr & ~(size - 1))


# ------------------------------------------------------------------ pytest

@pytest.mark.parametrize("words", [256, 512])
def test_sram_mem_core(words):
    run_cocotb(toplevel="sram_core_harness",
               sources=[ROOT / "rtl/core/mem_access.sv", HERE / "sram_core_harness.sv"],
               parameters={"WORDS": words}, test_module="test_sram_core",
               tag=f"sram_core_w{words}", extra_env={"SRAM_WORDS": str(words)})
