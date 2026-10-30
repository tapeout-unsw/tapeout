"""amoswap.w on the real bus: two real cores, arbiter, addr_decode, SRAM, MMIO.

DUT: core_bus_harness.sv (see ../core_bus), whose cores pass d_atomic to the
arbiter. The bench, bus monitor and tiny assembler come from test_core_bus.py,
so every cycle is also checked there: at most one grant, b_* match the winner,
round-robin fairness, and bus read data against a reference RAM.

  A1 amoswap_semantics      one core: rd gets the old word, memory gets rs2,
                            the address is rs1 with no offset, rd = x0 is
                            discarded, rd is usable by the next instruction
  A2 swap_race_both_cores   both cores swap the same words at the same time,
                            round after round: in every round exactly one core
                            sees the old 0 and the other sees the winner's tag
  A3 ttas_lock_counter      both cores take a test-and-test-and-set lock built
                            on amoswap (spin on lw, then swap) to increment a
                            shared counter; no lost updates, never two owners.
                            Logs bus reads of the lock word, to compare with
                            B5's MMIO LOCK.

Run with `python -m pytest -q sim/cocotb/integration/atomics`.
SEED=<n> replays the random fetch jitter.
"""

import sys
from pathlib import Path

import cocotb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core_bus"))
from test_core_bus import (  # noqa: E402
    CORE_ID, HALT, MMIO_BASE, RAW, SOURCES,
    addi, assemble, li, lui, lw, setup, sw,
)


def amoswap(rd, rs2, rs1):
    """amoswap.w rd, rs2, (rs1): funct5 00001, aq = rl = 0, funct3 010, opcode 0101111."""
    return (0b00001 << 27) | (rs2 << 20) | (rs1 << 15) | (0b010 << 12) | (rd << 7) | 0x2F


IDLE = ([HALT], {}, {})               # the other core in single-core tests


def core_tag_prologue():
    """x1 = MMIO base, x2 = CORE_ID, x6 = tag (1 or 2), x5 = result area
    (0x100 for core 0, 0x200 for core 1)."""
    return [lui(1, MMIO_BASE >> 12), lw(2, 1, CORE_ID), addi(6, 2, 1),
            addi(5, 0, 0x200), ("bne", 2, 0, "id1"), addi(5, 0, 0x100), "id1"]


@cocotb.test()
async def amoswap_semantics(dut):
    """A1: core 0 swaps three times at 0x300 and stores what it saw."""
    old, new, newer = 0x11111111, 0x22222222, 0x33333333
    p = assemble([
        addi(8, 0, 0x300), *li(6, new), *li(7, newer),
        amoswap(9, 6, 8),             # x9 = old word, mem = new
        addi(11, 9, 1),               # rd used straight away
        sw(9, 0, 0x104), sw(11, 0, 0x110),
        lw(10, 8, 0), sw(10, 0, 0x108),
        amoswap(0, 7, 8),             # rd = x0: result discarded
        sw(0, 0, 0x10C),
        HALT,
    ])
    prog = (p, {0x300: old}, {0x104: old, 0x108: new, 0x10C: 0, 0x110: old + 1, 0x300: newer})
    tb, halt = await setup(dut, prog, IDLE)
    await tb.run_cores()
    await tb.run_until_halt(halt, timeout=5000)
    tb.finish(await tb.check_ram(prog[2], "amoswap"))


@cocotb.test()
async def swap_race_both_cores(dut):
    """A2: both cores run the same program: for 16 rounds, swap their tag (1 or
    2) into word 0x300 + 4i and store the old value. Random fetch stalls vary
    how the two cores line up."""
    rounds = 16
    p = assemble(core_tag_prologue() + [
        addi(8, 0, 0x300), addi(12, 0, rounds),
        "round", amoswap(9, 6, 8), sw(9, 5, 0),
        addi(8, 8, 4), addi(5, 5, 4), addi(12, 12, -1), ("bne", 12, 0, "round"),
        HALT,
    ])
    prog = (p, {}, {})
    tb, halt = await setup(dut, prog, prog)
    cocotb.start_soon(tb.fetch_jitter(0.3))
    await tb.run_cores()
    await tb.run_until_halt(halt, timeout=20000)

    await tb.check_ram({}, "race")       # cores off the bus, boot port can read
    bad, wins = [], [0, 0]
    for i in range(rounds):
        r0, r1 = await tb.peek(0x100 + 4 * i), await tb.peek(0x200 + 4 * i)
        m = await tb.peek(0x300 + 4 * i)
        if r0 == 0 and r1 == 1 and m == 2:
            wins[0] += 1                  # core 0 swapped first
        elif r1 == 0 and r0 == 2 and m == 1:
            wins[1] += 1                  # core 1 swapped first
        else:
            bad.append(f"round {i}: core 0 saw {r0}, core 1 saw {r1}, memory {m} "
                       "(want one 0 and the winner's tag, memory = loser's tag)")
    dut._log.info(f"first swap per round: core 0 {wins[0]}, core 1 {wins[1]}")
    for b in bad:
        dut._log.error(b)
    tb.finish(bad)


@cocotb.test()
async def ttas_lock_counter(dut):
    """A3: n critical sections per core. Acquire: spin on lw until the lock
    word reads 0, then amoswap 1 into it; if the swap returns 1 someone got
    there first, so spin again. Inside: check nobody else is inside, then a
    read-modify-write of the counter. Release: store 0."""
    n = 20
    lock, counter, owner, intruder = 0x380, 0x300, 0x384, 0x388
    p = assemble(core_tag_prologue() + [
        addi(13, 0, lock), addi(14, 0, 1), addi(8, 0, counter), addi(15, 0, owner),
        addi(7, 0, 0), addi(12, 0, n),
        "acq", lw(3, 13, 0), ("bne", 3, 0, "acq"),       # test (cheap spin)
        amoswap(3, 14, 13), ("bne", 3, 0, "acq"),        # test-and-set
        lw(4, 15, 0), ("beq", 4, 0, "alone"),
        sw(6, 0, intruder),                               # two owners at once
        "alone", sw(6, 15, 0),
        lw(4, 8, 0), addi(4, 4, 1), sw(4, 8, 0),
        sw(0, 15, 0),
        sw(0, 13, 0),                                     # release
        addi(7, 7, 1), ("bne", 7, 12, "acq"),
        HALT,
    ])
    prog = (p, {}, {counter: 2 * n, lock: 0, owner: 0, intruder: 0})
    tb, halt = await setup(dut, prog, prog)
    cocotb.start_soon(tb.fetch_jitter(0.3))
    await tb.run_cores()
    await tb.run_until_halt(halt, timeout=40000)
    lock_reads = sum(1 for e in tb.bus_log if e[2] == lock and e[3] == 0)
    dut._log.info(f"lock word reads on the bus: {lock_reads} for {2 * n} acquisitions "
                  f"(B5's MMIO LOCK needs the bus on every spin too)")
    bad = await tb.check_ram(prog[2], "ttas")
    for b in bad:
        dut._log.error(b)
    tb.finish(bad)


def test_amoswap():
    sys.path.insert(0, str(HERE.parents[1] / "common"))
    from runner import run
    run("amoswap", SOURCES, "core_bus_harness", "test_amoswap", parameters={"RAW": RAW})
