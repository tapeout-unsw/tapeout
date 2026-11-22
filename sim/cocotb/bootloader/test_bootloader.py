"""cocotb unit tests for rtl/bootloader.sv.

Bytes are driven straight onto rx_data / rx_valid (one-cycle pulses with a
random gap), so no UART is involved. A monitor records every boot_req pulse
and checks that each lasts one cycle.

Header word, little-endian: bits 26:0 word count, bit 27 amo_off,
bits 31:28 chicken (1 = off: [3] ic0 [2] ic1 [1] dc0 [0] dc1).

  header_layout        BL-1: every chicken bit and amo_off set alone and all
                       together, on a first load after reset; they latch, and
                       the count ignores them (count bit 26 stays count)
  header_layout_reload the same headers as reloads from DONE
  load_n_words         N = 1, 2, 7, 64 words: writes go to 0, 4, 8, ... with
                       the little-endian word; loading stays high through the
                       last write's pulse; core_run rises only after it
  reload_from_done     four more bytes in DONE drop core_run, start a new load
                       and latch the new chicken / amo_off
  partial_word_in_done 1..3 stray bytes in DONE leave core_run high; the 4th
                       completes a word that is taken as a new count
  rx_claim_blocks      with rx_claim high, bytes in DONE are ignored entirely
                       (no partial-word progress); after release a full
                       header is needed
  count_zero           a count of 0: documents current behaviour (cores stay
                       held, the bootloader keeps taking words)
  reset_mid_load       rst during a load restarts from GET_LEN with chicken
                       and amo_off cleared

Run with `make test-cocotb BLOCK=bootloader`. SEED=<n> sets the seed.
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


def header(count, chicken=0, amo_off=0):
    return (chicken << 28) | (amo_off << 27) | (count & 0x7FF_FFFF)


class Bench:
    def __init__(self, dut, rng):
        self.d, self.rng = dut, rng
        self.writes = []
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        d = self.d
        prev = 0
        while True:
            await RisingEdge(d.clk)
            await Timer(1, "ns")
            v = int(d.boot_req.value)
            if v:
                assert not prev, "boot_req high for two cycles"
                assert int(d.loading.value) == 1 and int(d.core_run.value) == 0, \
                    "a write pulse must happen with loading high and the cores held"
                self.writes.append((int(d.boot_addr.value), int(d.boot_wdata.value)))
            prev = v

    async def reset(self):
        d = self.d
        await FallingEdge(d.clk)
        d.rst.value, d.rx_valid.value = 1, 0
        await FallingEdge(d.clk)
        d.rst.value = 0
        assert self.outs() == dict(core_run=0, loading=1, chicken=0, amo_off=0)

    async def byte(self, b, gap=None):
        d = self.d
        gap = self.rng.choice([0, 0, 1, 3]) if gap is None else gap
        for _ in range(gap):
            await FallingEdge(d.clk)
            d.rx_valid.value = 0
            d.rx_data.value = self.rng.getrandbits(8)      # junk while not valid
        await FallingEdge(d.clk)
        d.rx_data.value, d.rx_valid.value = b, 1
        await FallingEdge(d.clk)
        d.rx_valid.value = 0

    async def word(self, w):
        for k in range(4):
            await self.byte((w >> (8 * k)) & 0xFF)

    async def settle(self, n=3):
        for _ in range(n):
            await FallingEdge(self.d.clk)

    def outs(self):
        d = self.d
        return dict(core_run=int(d.core_run.value), loading=int(d.loading.value),
                    chicken=int(d.chicken.value), amo_off=int(d.amo_off.value))

    async def load(self, words, chicken=0, amo_off=0, first=True):
        """Send a header and the image; check the writes and the end state."""
        self.writes.clear()
        await self.word(header(len(words), chicken, amo_off))
        await self.settle(1)
        o = self.outs()
        assert o["chicken"] == chicken and o["amo_off"] == amo_off, f"header latched {o}"
        assert o["core_run"] == 0 and o["loading"] == 1
        for i, w in enumerate(words):
            await self.word(w)
            if i < len(words) - 1:
                assert int(self.d.core_run.value) == 0, "core_run before the last word"
        await self.settle()
        assert self.writes == [(4 * i, w) for i, w in enumerate(words)], "boot writes"
        o = self.outs()
        assert o == dict(core_run=1, loading=0, chicken=chicken, amo_off=amo_off), o


async def new_bench(dut, k=0):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    rng = random.Random(int(os.environ.get("SEED", "1")) + k)
    dut.rx_valid.value = 0
    dut.rx_data.value = 0
    dut.rx_claim.value = 0
    dut.rst.value = 1
    for _ in range(3):
        await FallingEdge(dut.clk)
    dut.rst.value = 0
    tb = Bench(dut, rng)
    o = tb.outs()
    assert o == dict(core_run=0, loading=1, chicken=0, amo_off=0), f"after reset {o}"
    return tb


HEADERS = [(0, 0), (8, 0), (4, 0), (2, 0), (1, 0), (0, 1), (0xF, 1), (0xA, 0), (5, 1), (0, 0)]


@cocotb.test()
async def header_layout(dut):
    """BL-1, first load after reset (GET_LEN): every bit alone and together.
    Count bit 26 is set in some headers to show it is count, not a flag."""
    tb = await new_bench(dut)
    for chicken, amo in HEADERS:
        await tb.reset()
        await tb.load([0x1000_0000 + chicken, 0x2000_0000 + amo], chicken, amo)
    # count bits 26:0 are all count: a huge count with every flag clear
    await tb.reset()
    await tb.word(header(0x400_0001))
    await tb.settle(1)
    assert tb.outs() == dict(core_run=0, loading=1, chicken=0, amo_off=0)
    assert int(dut.word_count.value) == 0x400_0001


@cocotb.test()
async def header_layout_reload(dut):
    """BL-1 again, each header as a reload from DONE."""
    tb = await new_bench(dut)
    for chicken, amo in HEADERS:
        await tb.load([0x3000_0000 + chicken, 0x4000_0000 + amo], chicken, amo)


@cocotb.test()
async def load_n_words(dut):
    tb = await new_bench(dut, 1)
    for n in (1, 2, 7, 64):
        await tb.load([tb.rng.getrandbits(32) for _ in range(n)])
    # the last write's pulse: loading stays high in that cycle (checked by the monitor)


@cocotb.test()
async def last_write_holds_loading(dut):
    """Cycle by cycle around the last byte: loading falls and core_run rises
    together, one cycle after the final boot_req pulse."""
    tb = await new_bench(dut, 2)
    await tb.word(header(1))
    for k in range(3):
        await tb.byte(0x11 * (k + 1))
    d = dut
    await FallingEdge(d.clk)
    d.rx_data.value, d.rx_valid.value = 0x44, 1
    await RisingEdge(d.clk)                      # the last byte is taken: state -> DONE, boot_req -> 1
    await Timer(1, "ns")
    assert int(d.boot_req.value) == 1
    assert int(d.loading.value) == 1 and int(d.core_run.value) == 0, "write cycle still loading"
    await RisingEdge(d.clk)
    await Timer(1, "ns")
    assert int(d.boot_req.value) == 0 and int(d.loading.value) == 0 and int(d.core_run.value) == 1
    d.rx_valid.value = 0


@cocotb.test()
async def reload_from_done(dut):
    tb = await new_bench(dut, 3)
    await tb.load([1, 2, 3], chicken=0, amo_off=0)
    tb.writes.clear()
    await tb.word(header(2, chicken=0b0110, amo_off=1))
    await tb.settle(1)
    assert tb.outs() == dict(core_run=0, loading=1, chicken=0b0110, amo_off=1)
    await tb.word(0xAAAA_AAAA); await tb.word(0xBBBB_BBBB)
    await tb.settle()
    assert tb.writes == [(0, 0xAAAA_AAAA), (4, 0xBBBB_BBBB)]
    assert tb.outs()["core_run"] == 1


@cocotb.test()
async def partial_word_in_done(dut):
    tb = await new_bench(dut, 4)
    await tb.load([7])
    for n in (1, 2, 3):
        await tb.byte(0x02 if n == 1 else 0x00)
        await tb.settle()
        assert tb.outs()["core_run"] == 1, f"{n} stray byte(s) must not drop core_run"
    await tb.byte(0x00)                          # completes the word 0x00000002 = count 2
    await tb.settle(1)
    assert tb.outs()["core_run"] == 0, "4th byte is a new count: reload"
    await tb.word(5); await tb.word(6)
    await tb.settle()
    assert tb.outs()["core_run"] == 1


@cocotb.test()
async def rx_claim_blocks(dut):
    tb = await new_bench(dut, 5)
    await tb.load([9, 9])
    tb.writes.clear()
    dut.rx_claim.value = 1
    for b in range(10):                          # more than a word's worth
        await tb.byte(b)
    await tb.settle()
    assert tb.outs()["core_run"] == 1, "bytes while rx_claim is high must not reload"
    assert not tb.writes, "no memory writes while rx_claim is high"
    dut.rx_claim.value = 0
    # the claimed bytes made no partial-word progress: 3 bytes are not enough
    for b in (1, 0, 0):
        await tb.byte(b)
    await tb.settle()
    assert tb.outs()["core_run"] == 1, "claimed bytes counted towards a word"
    await tb.byte(0)
    await tb.settle(1)
    assert tb.outs()["core_run"] == 0, "a full header after release reloads"
    await tb.word(0x1234_5678)
    await tb.settle()
    assert tb.outs()["core_run"] == 1


@cocotb.test()
async def rx_claim_before_load(dut):
    """rx_claim is only meant to be high in DONE, but if it is high in GET_LEN
    bytes are ignored there too."""
    tb = await new_bench(dut, 6)
    dut.rx_claim.value = 1
    for b in range(8):
        await tb.byte(0xFF)
    dut.rx_claim.value = 0
    await tb.settle()
    assert tb.outs() == dict(core_run=0, loading=1, chicken=0, amo_off=0)
    await tb.load([0xCAFE_F00D], chicken=0b1001)


@cocotb.test()
async def count_zero(dut):
    """A count of 0 never matches words_got + 1, so the bootloader stays in
    GET_PROG and keeps writing every word it gets: the cores stay held. This
    documents current behaviour; whether 0 should mean 'run what is already
    in memory' is a spec decision (see the coverage report)."""
    tb = await new_bench(dut, 7)
    tb.writes.clear()
    await tb.word(header(0, chicken=0b0011))
    for w in range(5):
        await tb.word(0x100 + w)
    await tb.settle()
    assert tb.outs()["core_run"] == 0 and tb.outs()["loading"] == 1
    assert tb.writes == [(4 * i, 0x100 + i) for i in range(5)]


@cocotb.test()
async def reset_mid_load(dut):
    tb = await new_bench(dut, 8)
    await tb.word(header(10, chicken=0xF, amo_off=1))
    await tb.word(1); await tb.byte(0x55)       # a partial word too
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await tb.settle(2)
    dut.rst.value = 0
    await tb.settle(1)
    assert tb.outs() == dict(core_run=0, loading=1, chicken=0, amo_off=0)
    await tb.load([0xD00D_F00D, 3])             # a fresh header is needed (byte_idx cleared)


def test_bootloader():
    """pytest entry point."""
    run("bootloader", ["rtl/bootloader.sv"], "bootloader", "test_bootloader")
