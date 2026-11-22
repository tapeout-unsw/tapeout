"""cocotb unit tests for rtl/core/alu.sv.

The ALU is combinational: each check drives a, b and ctrl, waits 1 ns and
compares res with a Python model of the RV32 operation.

  edge_values      every operation on every pair of edge values
                   (0, 1, -1, INT_MIN, INT_MAX, 0x80, ...), shift amounts 0..31
                   and b with bits above [4:0] set (they must be ignored)
  undefined_ctrl   ctrl 10..15 returns 0 (the default arm)
  random_ops       random a, b and ctrl against the model

Run with `make test-cocotb BLOCK=alu`. SEED=<n> sets the random seed,
OPS=<n> the number of random checks.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

M = 0xFFFF_FFFF
NAMES = ["ADD", "SUB", "AND", "OR", "XOR", "SLL", "SRL", "SRA", "SLT", "SLTU"]
EDGES = [0, 1, 2, 0x7F, 0x80, 0xFF, 0x7FFF_FFFF, 0x8000_0000, 0x8000_0001,
         0xFFFF_FFFE, M, 0x5555_5555, 0xAAAA_AAAA, 31, 32, 0x1F | 0xFFE0]


def s32(x):
    return x - (1 << 32) if x & 0x8000_0000 else x


def model(a, b, ctrl):
    sh = b & 31
    if ctrl == 0: return (a + b) & M
    if ctrl == 1: return (a - b) & M
    if ctrl == 2: return a & b
    if ctrl == 3: return a | b
    if ctrl == 4: return a ^ b
    if ctrl == 5: return (a << sh) & M
    if ctrl == 6: return a >> sh
    if ctrl == 7: return (s32(a) >> sh) & M
    if ctrl == 8: return int(s32(a) < s32(b))
    if ctrl == 9: return int(a < b)
    return 0


async def check(dut, a, b, ctrl):
    dut.a.value, dut.b.value, dut.ctrl.value = a, b, ctrl
    await Timer(1, "ns")
    got, exp = int(dut.res.value), model(a, b, ctrl)
    name = NAMES[ctrl] if ctrl < 10 else f"ctrl={ctrl}"
    assert got == exp, f"{name} a={a:#010x} b={b:#010x}: got {got:#010x}, expected {exp:#010x}"


@cocotb.test()
async def edge_values(dut):
    """Every operation on every pair of edge values, and every shift amount."""
    for ctrl in range(10):
        for a in EDGES:
            for b in EDGES:
                await check(dut, a, b, ctrl)
    for ctrl in (5, 6, 7):
        for a in (0x8000_0001, 0x7FFF_FFFF, M, 0x1234_5678):
            for sh in range(32):
                await check(dut, a, sh, ctrl)
                await check(dut, a, sh | 0xFFFF_FFE0, ctrl)   # upper b bits ignored


@cocotb.test()
async def undefined_ctrl(dut):
    """ctrl values with no operation return 0, whatever a and b are."""
    for ctrl in range(10, 16):
        for a, b in ((M, M), (0x1234_5678, 0x9ABC_DEF0), (0, 0)):
            await check(dut, a, b, ctrl)


@cocotb.test()
async def random_ops(dut):
    """Random a, b and ctrl against the model."""
    seed = int(os.environ.get("SEED", "1"))
    dut._log.info(f"SEED={seed}")
    rng = random.Random(seed)
    for _ in range(int(os.environ.get("OPS", "20000"))):
        await check(dut, rng.getrandbits(32), rng.getrandbits(32), rng.randrange(16))


def test_alu():
    """pytest entry point."""
    run("alu", ["rtl/core/alu.sv"], "alu", "test_alu")
