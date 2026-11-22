"""cocotb unit tests for rtl/core/imm_gen.sv.

  each_format      I, S, B, U and J immediates from real encodings, positive,
                   negative and extreme values, compared with the value the
                   instruction was assembled from
  undefined_ctrl   ctrl 5..7 returns 0 (the default arm)
  random_insts     random instruction words against a bit-slicing model

Run with `make test-cocotb BLOCK=imm_gen`. SEED=<n> sets the random seed.
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
I, S, B, U, J = range(5)


def bits(x, hi, lo):
    return (x >> lo) & ((1 << (hi - lo + 1)) - 1)


def sext(v, n):
    v &= (1 << n) - 1
    return (v - (1 << n)) & M if v >> (n - 1) else v


# Encoders: the immediate placed in an instruction word, other fields random-ish.
def enc_i(imm): return ((imm & 0xFFF) << 20) | (5 << 15) | (3 << 7) | 0x13
def enc_s(imm): return (bits(imm, 11, 5) << 25) | (6 << 20) | (5 << 15) | (2 << 12) | (bits(imm, 4, 0) << 7) | 0x23
def enc_b(imm): return (bits(imm, 12, 12) << 31) | (bits(imm, 10, 5) << 25) | (6 << 20) | (5 << 15) \
                       | (bits(imm, 4, 1) << 8) | (bits(imm, 11, 11) << 7) | 0x63
def enc_u(imm): return (imm & 0xFFFF_F000) | (3 << 7) | 0x37
def enc_j(imm): return (bits(imm, 20, 20) << 31) | (bits(imm, 10, 1) << 21) | (bits(imm, 11, 11) << 20) \
                       | (bits(imm, 19, 12) << 12) | (1 << 7) | 0x6F


def model(inst, ctrl):
    if ctrl == I: return sext(bits(inst, 31, 20), 12)
    if ctrl == S: return sext((bits(inst, 31, 25) << 5) | bits(inst, 11, 7), 12)
    if ctrl == B: return sext((bits(inst, 31, 31) << 12) | (bits(inst, 7, 7) << 11)
                              | (bits(inst, 30, 25) << 5) | (bits(inst, 11, 8) << 1), 13)
    if ctrl == U: return inst & 0xFFFF_F000
    if ctrl == J: return sext((bits(inst, 31, 31) << 20) | (bits(inst, 19, 12) << 12)
                              | (bits(inst, 20, 20) << 11) | (bits(inst, 30, 21) << 1), 21)
    return 0


async def get(dut, inst, ctrl):
    dut.inst.value, dut.ctrl.value = inst, ctrl
    await Timer(1, "ns")
    return int(dut.imm.value)


@cocotb.test()
async def each_format(dut):
    """Assemble each format with known immediates and read them back."""
    cases = {
        I: [0, 1, -1, 2047, -2048, 0x555, -0x2AB],
        S: [0, 1, -1, 2047, -2048, 0x2A5, -100],
        B: [0, 2, -2, 4094, -4096, 0x7FE, -0x800, 0x554],
        U: [0, 0x1000, 0xFFFF_F000, 0x8000_0000, 0x7FFF_F000, 0x1234_5000],
        J: [0, 2, -2, (1 << 20) - 2, -(1 << 20), 0x7FE, 0x800, 0xFF000, -0x55554],
    }
    enc = {I: enc_i, S: enc_s, B: enc_b, U: enc_u, J: enc_j}
    for ctrl, values in cases.items():
        for v in values:
            exp = v & M
            got = await get(dut, enc[ctrl](exp), ctrl)
            assert got == exp, f"format {'ISBUJ'[ctrl]} value {v}: got {got:#010x}, expected {exp:#010x}"


@cocotb.test()
async def undefined_ctrl(dut):
    """ctrl 5..7 select no format and return 0."""
    for ctrl in (5, 6, 7):
        for inst in (M, 0x8000_0000, 0x1234_5678):
            assert await get(dut, inst, ctrl) == 0, f"ctrl={ctrl}"


@cocotb.test()
async def random_insts(dut):
    """Random instruction words, every ctrl value, against the model."""
    rng = random.Random(int(os.environ.get("SEED", "1")))
    for _ in range(5000):
        inst, ctrl = rng.getrandbits(32), rng.randrange(8)
        got, exp = await get(dut, inst, ctrl), model(inst, ctrl)
        assert got == exp, f"inst={inst:#010x} ctrl={ctrl}: got {got:#010x}, expected {exp:#010x}"


def test_imm_gen():
    """pytest entry point."""
    run("imm_gen", ["rtl/core/imm_gen.sv"], "imm_gen", "test_imm_gen")
