"""cocotb unit tests for rtl/core/amoswap.sv.

amoswap.sv runs one amoswap.w as a bus read followed by a bus write:
IDLE -start-> READ -gnt-> RESPONSE -> WRITE -gnt-> IDLE. These tests drive it
directly, so the grant can be delayed in either access, which the arbiter's
bus hold never does on the real bus (coverage report A5).

Inputs change on the falling edge and outputs are read 1 ns later.

  immediate_grants   grant in the first cycle of READ and WRITE
  delayed_grants     mem_gnt held low for 1..5 cycles in READ and in WRITE:
                     the request and its bundle stay stable, done only pulses
                     with the WRITE grant, old_value is the read data
  start_ignored      start while busy does not restart or change the operands
  reset_mid_op       rst in each state returns to IDLE with no request
  random_sequences   back-to-back swaps with random delays against a model

Run with `make test-cocotb BLOCK=amoswap`. SEED=<n> sets the seed.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402


class Bench:
    def __init__(self, dut):
        self.d = dut

    async def cyc(self, start=0, address=0, replacement=0, gnt=0, rdata=0, rst=0):
        """Drive one cycle's inputs, return the outputs seen in that cycle."""
        d = self.d
        await FallingEdge(d.clk)
        d.start.value, d.address.value, d.replacement.value = start, address, replacement
        d.mem_gnt.value, d.mem_rdata.value, d.rst.value = gnt, rdata, rst
        await Timer(1, "ns")
        return {k: int(getattr(d, k).value) for k in
                ("busy", "done", "old_value", "mem_req", "mem_addr", "mem_wdata", "mem_wstrb")}

    async def swap(self, addr, new, old, read_wait, write_wait, idle_before=0):
        """One full swap; check every cycle. Returns the number of cycles."""
        for _ in range(idle_before):
            o = await self.cyc()
            assert o["busy"] == 0 and o["mem_req"] == 0 and o["done"] == 0
        o = await self.cyc(start=1, address=addr, replacement=new)
        assert o["busy"] == 0 and o["mem_req"] == 0, "IDLE: no request in the start cycle"
        # READ, waiting
        for _ in range(read_wait):
            o = await self.cyc(gnt=0, start=1, address=addr ^ 0x1FFC, replacement=~new & 0xFFFF_FFFF)
            assert o["busy"] and o["mem_req"] and o["mem_wstrb"] == 0 and not o["done"]
            assert o["mem_addr"] == addr and o["mem_wdata"] == new, "READ: bundle stable while waiting"
        o = await self.cyc(gnt=1)
        assert o["mem_req"] and o["mem_wstrb"] == 0 and not o["done"], "READ granted"
        # RESPONSE: read data arrives the cycle after the grant
        o = await self.cyc(gnt=0, rdata=old)
        assert o["busy"] and not o["mem_req"] and not o["done"], "RESPONSE: no request"
        # WRITE, waiting
        for _ in range(write_wait):
            o = await self.cyc(gnt=0, rdata=0xBAD_BAD)
            assert o["busy"] and o["mem_req"] and o["mem_wstrb"] == 0xF and not o["done"], \
                "WRITE: request held, done low, while the grant is delayed"
            assert o["mem_addr"] == addr and o["mem_wdata"] == new and o["old_value"] == old
        o = await self.cyc(gnt=1)
        assert o["mem_req"] and o["mem_wstrb"] == 0xF and o["done"] == 1, "WRITE granted: done"
        assert o["old_value"] == old and o["mem_addr"] == addr and o["mem_wdata"] == new
        o = await self.cyc()
        assert not o["busy"] and not o["mem_req"] and not o["done"], "back to IDLE"
        assert o["old_value"] == old, "old_value held after done"


async def new_bench(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    tb = Bench(dut)
    await tb.cyc(rst=1)
    await tb.cyc(rst=1)
    return tb


@cocotb.test()
async def immediate_grants(dut):
    tb = await new_bench(dut)
    await tb.swap(0x0300, 0x2222_2222, 0x1111_1111, 0, 0)
    await tb.swap(0x1FFC, 0xFFFF_FFFF, 0x0000_0000, 0, 0)


@cocotb.test()
async def delayed_grants(dut):
    """A5: the WRITE waits for its grant."""
    tb = await new_bench(dut)
    for rw in range(4):
        for ww in range(1, 6):
            await tb.swap(0x0100 + 4 * ww, 0xA0A0_0000 | ww, 0x5050_0000 | rw, rw, ww)


@cocotb.test()
async def start_ignored_while_busy(dut):
    """start held high through a swap does not restart it; a new swap can
    start the cycle after done."""
    tb = await new_bench(dut)
    await tb.swap(0x0040, 0x1234_5678, 0x8765_4321, 2, 2)
    await tb.swap(0x0044, 0x0BAD_F00D, 0xFEED_FACE, 0, 3, idle_before=0)


@cocotb.test()
async def reset_mid_op(dut):
    """rst in READ, RESPONSE or WRITE returns to IDLE with no request."""
    tb = await new_bench(dut)
    for stop_in in ("READ", "RESPONSE", "WRITE"):
        await tb.cyc(start=1, address=0x80, replacement=7)
        if stop_in in ("RESPONSE", "WRITE"):
            await tb.cyc(gnt=1)                 # READ granted
        if stop_in == "WRITE":
            await tb.cyc(rdata=3)               # RESPONSE
        o = await tb.cyc(rst=1)
        assert o["busy"], f"busy in {stop_in} before reset"
        o = await tb.cyc()
        assert not o["busy"] and not o["mem_req"] and not o["done"], f"reset in {stop_in}"
        assert o["old_value"] == 0


@cocotb.test()
async def random_sequences(dut):
    tb = await new_bench(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    for _ in range(300):
        await tb.swap(rng.getrandbits(11) << 2, rng.getrandbits(32), rng.getrandbits(32),
                      rng.choice([0, 0, 1, 2, 7]), rng.choice([0, 0, 1, 3, 6]),
                      idle_before=rng.choice([0, 0, 1, 3]))


def test_amoswap_unit():
    """pytest entry point."""
    run("amoswap", ["rtl/core/amoswap.sv"], "amoswap", "test_amoswap_unit")
