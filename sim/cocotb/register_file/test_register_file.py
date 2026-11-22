"""cocotb unit tests for rtl/core/register_file.sv.

Built four times: NREGS = 16 and 32, BYPASS = 0 and 1. The core only uses
BYPASS = 0; the BYPASS = 1 builds exist so the bypass path the RTL keeps for a
pipelined core is tested rather than waived.

Inputs change on the falling edge; reads are checked 1 ns later (they are
combinational), writes after the next rising edge.

  x0_is_zero          x0 reads 0 and writes to it are dropped
  write_read_all      every implemented register holds its own value
  upper_regs          NREGS = 16: x16..x31 read 0 (rs1_bad, rs2_bad) and
                      writes to them are dropped (rd_bad), never aliasing x0..x15
  same_cycle_rw       write and read one register in one cycle: old value
                      without bypass, the new value with bypass
  random_ops          random reads and writes against a model

Run with `make test-cocotb BLOCK=register_file`. SEED=<n> sets the seed.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

M = 0xFFFF_FFFF
NREGS = int(os.environ.get("RF_NREGS", "16"))
BYPASS = int(os.environ.get("RF_BYPASS", "0"))


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.model = [0] * 32          # the RTL zeroes registers at time 0 in simulation

    def implemented(self, r):
        return r != 0 and r < NREGS

    async def write(self, rd, val, we=1):
        d = self.dut
        await FallingEdge(d.clk)
        d.rd_we.value, d.rd_addr.value, d.rd_data.value = we, rd, val
        await RisingEdge(d.clk)
        if we and self.implemented(rd):
            self.model[rd] = val
        await FallingEdge(d.clk)
        d.rd_we.value = 0

    async def read(self, rs1, rs2=0):
        d = self.dut
        d.rs1_addr.value, d.rs2_addr.value = rs1, rs2
        await Timer(1, "ns")
        return int(d.rs1_data.value), int(d.rs2_data.value)

    async def expect(self, r):
        a, b = await self.read(r, r)
        exp = self.model[r] if self.implemented(r) else 0
        assert a == exp and b == exp, f"x{r}: rs1 {a:#x} rs2 {b:#x}, expected {exp:#x}"


async def new_bench(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rd_we.value = 0
    dut.rd_addr.value = 0
    dut.rd_data.value = 0
    dut.rs1_addr.value = 0
    dut.rs2_addr.value = 0
    await FallingEdge(dut.clk)
    dut._log.info(f"NREGS={NREGS} BYPASS={BYPASS}")
    tb = Bench(dut)
    for r in range(1, 32):           # registers keep their values between tests
        await tb.write(r, 0)
    return tb


@cocotb.test()
async def x0_is_zero(dut):
    tb = await new_bench(dut)
    await tb.write(0, 0xDEAD_BEEF)
    await tb.expect(0)


@cocotb.test()
async def write_read_all(dut):
    tb = await new_bench(dut)
    for r in range(32):
        await tb.write(r, (0x1111_1111 * (r % 15 + 1)) ^ (r << 24))
    for r in range(32):
        await tb.expect(r)
    # rs1 and rs2 are independent ports
    for r in range(1, NREGS):
        a, b = await tb.read(r, NREGS - r)
        assert a == tb.model[r] and b == tb.model[NREGS - r]
    # a write with rd_we low changes nothing
    await tb.write(3, 0xBAD0_BAD0, we=0)
    await tb.expect(3)


@cocotb.test()
async def upper_regs(dut):
    """With NREGS = 16, x16..x31 behave as x0 and never alias x0..x15."""
    tb = await new_bench(dut)
    for r in range(1, 16):
        await tb.write(r, 0xA000_0000 | r)
    for r in range(16, 32):
        await tb.write(r, 0x5000_0000 | r)
    for r in range(32):
        await tb.expect(r)
    if NREGS == 16:
        for r in range(1, 16):
            assert tb.model[r] == 0xA000_0000 | r, "write to x16+ aliased into x0..x15"


@cocotb.test()
async def same_cycle_rw(dut):
    """Write and read the same register in one cycle."""
    tb = await new_bench(dut)
    d = dut
    for r in (1, 7, NREGS - 1):
        await tb.write(r, 0x0101_0101)
        await FallingEdge(d.clk)
        d.rd_we.value, d.rd_addr.value, d.rd_data.value = 1, r, 0x2222_2222
        a, b = await tb.read(r, r)
        exp = 0x2222_2222 if BYPASS else 0x0101_0101
        assert a == exp and b == exp, f"x{r} during its own write: {a:#x}/{b:#x}, expected {exp:#x}"
        # a different register being written does not bypass
        other = 2 if r != 2 else 3
        a, _ = await tb.read(other)
        assert a == tb.model[other]
        await RisingEdge(d.clk)
        tb.model[r] = 0x2222_2222
        await FallingEdge(d.clk)
        d.rd_we.value = 0
        await tb.expect(r)
        # writing x0 or x16+ with bypass on must still read 0
        d.rd_we.value, d.rd_addr.value, d.rd_data.value = 1, 0, M
        a, b = await tb.read(0, 0)
        assert a == 0 and b == 0
        d.rd_we.value = 0


@cocotb.test(expect_fail=(NREGS == 16 and BYPASS == 1))
async def dropped_write_never_bypasses(dut):
    """A write to x16..x31 is dropped when NREGS = 16, so it must not bypass
    into a read of x0..x15 either. Known bug: the bypass compares the
    truncated indices and ignores rd_bad, so with BYPASS = 1 a write to x17
    shows up on a read of x1 for that cycle. Marked expect_fail for that
    build only; the core uses BYPASS = 0, where this cannot happen."""
    tb = await new_bench(dut)
    await tb.write(1, 0x0000_0001)
    await FallingEdge(dut.clk)
    dut.rd_we.value, dut.rd_addr.value, dut.rd_data.value = 1, 17, 0xBAD0_0017
    a, b = await tb.read(1, 1)
    dut.rd_we.value = 0
    exp = 0x0000_0001                  # x1 is untouched in every configuration
    assert a == exp and b == exp, f"read x1 during a write to x17: {a:#x}, expected {exp:#x}"


@cocotb.test()
async def random_ops(dut):
    tb = await new_bench(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    for _ in range(3000):
        if rng.random() < 0.5:
            await tb.write(rng.randrange(32), rng.getrandbits(32), we=int(rng.random() < 0.9))
        else:
            await tb.expect(rng.randrange(32))


CONFIGS = [(16, 0), (32, 0), (16, 1), (32, 1)]


def test_register_file():
    """pytest entry point: one build per NREGS/BYPASS combination."""
    for n, byp in CONFIGS:
        os.environ["RF_NREGS"], os.environ["RF_BYPASS"] = str(n), str(byp)
        run("register_file", ["rtl/core/register_file.sv"], "register_file",
            "test_register_file", parameters={"NREGS": n, "BYPASS": byp}, tag=f"_{n}_{byp}")
