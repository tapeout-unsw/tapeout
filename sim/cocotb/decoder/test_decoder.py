"""cocotb unit tests for rtl/core/decoder.sv.

The decoder is combinational. Each check drives inst and amo_off, waits 1 ns,
and compares every output with a Python table of the RV32 base ISA plus
amoswap.w, written from the spec rather than from the RTL.

  fields              opc, rd, rs1, rs2, func3, func7 are the raw bit fields
  every_opcode        every opcode/func3/func7[5] combination the core uses
  illegal_opcodes     all 128 opcodes: unknown ones leave every control low
  amoswap_amo_off     amoswap.w with amo_off 0 and 1 (AMO-1: with amo_off = 1
                      it decodes as a no-op: no register write, no memory
                      access), and near-miss encodings (wrong funct3, wrong
                      funct5) that must not decode as amoswap
  random_insts        random instruction words against the table

Run with `make test-cocotb BLOCK=decoder`. SEED=<n> sets the random seed.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

CTRL = ("reg_write", "imm_sel", "alu_op", "alu_src", "branch", "jmp", "jmpr",
        "mem_read", "mem_write", "lui", "auipc", "amo_swap")
# ALU op codes from alu.sv
ADD, SUB, AND, OR, XOR, SLL, SRL, SRA, SLT, SLTU = range(10)
ALU_F3 = {0: ADD, 7: AND, 6: OR, 4: XOR, 1: SLL, 5: SRL, 2: SLT, 3: SLTU}
IMM_I, IMM_S, IMM_B, IMM_U, IMM_J = range(5)
OP, OP_IMM, LOAD, STORE, BRANCH, LUI, JAL, JALR, AUIPC, AMO = (
    0x33, 0x13, 0x03, 0x23, 0x63, 0x37, 0x6F, 0x67, 0x17, 0x2F)


def expected(inst, amo_off):
    """The control word the RV32 spec implies for this instruction."""
    opc, f3, f7 = inst & 0x7F, (inst >> 12) & 7, inst >> 25
    c = dict.fromkeys(CTRL, 0)
    if opc == OP:
        op = ALU_F3[f3]
        if f3 == 0 and f7 & 0x20: op = SUB
        if f3 == 5 and f7 & 0x20: op = SRA
        c.update(reg_write=1, alu_op=op)
    elif opc == OP_IMM:
        op = ALU_F3[f3]
        if f3 == 5 and f7 & 0x20: op = SRA          # srai
        c.update(reg_write=1, alu_src=1, imm_sel=IMM_I, alu_op=op)
    elif opc == LOAD:
        c.update(reg_write=1, alu_src=1, imm_sel=IMM_I, mem_read=1)
    elif opc == STORE:
        c.update(alu_src=1, imm_sel=IMM_S, mem_write=1)
    elif opc == BRANCH:
        c.update(imm_sel=IMM_B, branch=1)
    elif opc == LUI:
        c.update(reg_write=1, imm_sel=IMM_U, lui=1)
    elif opc == JAL:
        c.update(reg_write=1, imm_sel=IMM_J, jmp=1)
    elif opc == JALR:
        c.update(reg_write=1, imm_sel=IMM_I, jmpr=1)
    elif opc == AUIPC:
        c.update(reg_write=1, imm_sel=IMM_U, auipc=1)
    elif opc == AMO:
        if not amo_off and f3 == 2 and (inst >> 27) == 0b00001:
            c.update(amo_swap=1, reg_write=1, mem_read=1)
    return c


async def check(dut, inst, amo_off=0):
    dut.inst.value, dut.amo_off.value = inst, amo_off
    await Timer(1, "ns")
    exp = expected(inst, amo_off)
    got = {k: int(getattr(dut, k).value) for k in CTRL}
    assert got == exp, (f"inst={inst:#010x} amo_off={amo_off}: "
                        + ", ".join(f"{k} got {got[k]} exp {exp[k]}" for k in CTRL if got[k] != exp[k]))
    fields = dict(opc=inst & 0x7F, rd=(inst >> 7) & 31, func3=(inst >> 12) & 7,
                  rs1=(inst >> 15) & 31, rs2=(inst >> 20) & 31, func7=inst >> 25)
    for k, v in fields.items():
        assert int(getattr(dut, k).value) == v, f"inst={inst:#010x}: field {k}"


def amoswap(rd, rs2, rs1, f5=0b00001, f3=2, aqrl=0):
    return (f5 << 27) | (aqrl << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | AMO


@cocotb.test()
async def every_opcode(dut):
    """Every opcode, every func3, func7[5] both ways, rd/rs fields varied."""
    for opc in (OP, OP_IMM, LOAD, STORE, BRANCH, LUI, JAL, JALR, AUIPC):
        for f3 in range(8):
            for f7 in (0x00, 0x20, 0x7F, 0x01):
                inst = (f7 << 25) | (0x15 << 20) | (0x0A << 15) | (f3 << 12) | (0x1B << 7) | opc
                await check(dut, inst)
                await check(dut, inst, amo_off=1)      # amo_off must not affect the base ISA


@cocotb.test()
async def illegal_opcodes(dut):
    """All 128 opcodes with a few bodies; unknown ones decode to all-zero controls."""
    for opc in range(128):
        for body in (0, 0xFFFF_FF80, 0x0080_2000):
            await check(dut, body | opc)
            await check(dut, body | opc, amo_off=1)


@cocotb.test()
async def amoswap_amo_off(dut):
    """amoswap.w decodes with amo_off = 0 and is a no-op with amo_off = 1;
    other AMOs and other widths never decode as amoswap."""
    for aqrl in range(4):                        # aq/rl bits are don't-care
        inst = amoswap(rd=5, rs2=6, rs1=7, aqrl=aqrl)
        await check(dut, inst, amo_off=0)
        assert int(dut.amo_swap.value) == 1
        await check(dut, inst, amo_off=1)
        assert int(dut.amo_swap.value) == 0 and int(dut.reg_write.value) == 0 \
            and int(dut.mem_read.value) == 0 and int(dut.mem_write.value) == 0, \
            "AMO-1: amoswap with amo_off = 1 must not write a register or touch memory"
    for f3 in range(8):                          # .d, .b etc.: not amoswap.w
        for f5 in (0b00000, 0b00001, 0b00010, 0b00011, 0b01000, 0b11100, 0b10001):
            for off in (0, 1):
                await check(dut, amoswap(1, 2, 3, f5=f5, f3=f3), off)


@cocotb.test()
async def random_insts(dut):
    """Random words, half of them with a valid opcode, against the table."""
    rng = random.Random(int(os.environ.get("SEED", "1")))
    opcs = [OP, OP_IMM, LOAD, STORE, BRANCH, LUI, JAL, JALR, AUIPC, AMO]
    for _ in range(10000):
        inst = rng.getrandbits(32)
        if rng.random() < 0.6:
            inst = (inst & ~0x7F) | rng.choice(opcs)
        await check(dut, inst, rng.getrandbits(1))


def test_decoder():
    """pytest entry point."""
    run("decoder", ["rtl/core/decoder.sv"], "decoder", "test_decoder")
