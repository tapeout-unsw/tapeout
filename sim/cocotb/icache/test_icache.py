"""cocotb tests for rtl/cache/icache.sv.

The testbench plays the core (i_req, i_addr, flush) and the bus (ic_gnt,
bus_rdata, backed by a random memory), and checks the cache against
icache_model.py, which follows the behaviour in the header of icache.sv.

Every fetch is checked three ways:
  data    i_rdata is the word in memory
  timing  a predicted hit answers in the cycle i_req is raised, without a bus
          request; a predicted miss raises ic_req and answers exactly one
          cycle after its single ic_gnt
  bus     every cycle: ic_wdata and ic_wstrb are zero, ic_addr is
          {i_addr[12:2], 2'b00}, and ic_req is held until ic_gnt

Run with `make test-cocotb BLOCK=icache`. Options, as environment variables:
  TAG_BITS=<n>    test a narrower tag than the spec's RAW-5 (6 bits at
                  RAW=11). Fetches then stay below 2^(TAG_BITS+5) bytes, the
                  range a tag that wide can tell apart.
  SEED=<n>        random seed (default 1); every run prints the one it used
  FETCHES=<n>     length of the random fetch stream (default 4000)
  TRACE=<file>    also replay a recorded fetch-address trace, one hex address
                  per line, as tb/icache_tb.sv does with +TRACE
  WAVES=1         write a waveform to sim/obj_dir/cocotb_icache_raw<RAW>/
"""

import os
import random
import sys
from collections import namedtuple
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402
from icache_model import SETS, WAYS, ICacheModel  # noqa: E402

CLK_NS = 10
FETCH_TIMEOUT = 50  # cycles a single fetch may take before the test gives up
LOAD_CYCLES = 20    # length of a simulated bootloader reload

# Tag width. The spec gives RAW-5 bits: a 39-bit line is 1 valid + 6 tag +
# 32 data, with 2 KiB of RAM (RAW=11). TAG_BITS=<n> overrides it.
TAG_BITS = os.environ.get("TAG_BITS", "spec")

Cycle = namedtuple("Cycle", "i_valid i_rdata ic_req ic_gnt ic_addr")


def addr_of(index, tag):
    """Byte address of the word with this set index and tag."""
    return (tag << 5) | (index << 2)


class Bench:
    """Drives the core side, models the bus side, and checks the interface.

    All of it happens in step(), once per clock cycle: inputs change on the
    falling edge, outputs are sampled just after, and the cache registers them
    on the next rising edge. Stimulus and bus timing come from two separate
    generators seeded from SEED, so a run replays exactly.
    """

    def __init__(self, dut):
        self.dut = dut
        self.raw = int(dut.RAW.value)
        self.words = 1 << (self.raw - 2)
        self.tag_bits = self.raw - 5 if TAG_BITS == "spec" else int(TAG_BITS)
        self.span = 1 << min(self.raw, self.tag_bits + 5)  # fetch addresses stay below
        seed = int(os.environ.get("SEED", "1"))
        dut._log.info(f"RAW={self.raw} TAG_BITS={self.tag_bits} SEED={seed} (replay with SEED={seed})")
        self.rng = random.Random(seed)              # stimulus
        self.bus_rng = random.Random(seed + 1)      # bus delays and noise
        self.mem = [self.rng.getrandbits(32) for _ in range(self.words)]
        self.model = ICacheModel(self.tag_bits)
        self.max_wait = 3       # the bus grants 0 to max_wait cycles after ic_req
        self.loading = False    # bootloader running: the bus grants nobody
        self.wait_left = 0
        self.rdata_next = None  # word on bus_rdata next cycle, after a grant
        self.waiting = False    # last cycle had ic_req without ic_gnt
        self.last_ic_addr = 0
        self.fetches = self.hits = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_NS, unit="ns").start())
        await self.reset()

    async def reset(self):
        for _ in range(3):
            await self.step(rst=1)
        self.model.flush()

    async def step(self, i_req=0, i_addr=0, flush=0, rst=0):
        """Run one clock cycle with these inputs; return the outputs in it."""
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.rst.value = rst
        dut.flush.value = flush
        dut.i_req.value = i_req
        dut.i_addr.value = i_addr
        # bus_rdata only carries data the cycle after a grant. Drive noise at
        # other times so a cache that latches it in the wrong cycle is caught.
        if self.rdata_next is None:
            dut.bus_rdata.value = self.bus_rng.getrandbits(32)
        else:
            dut.bus_rdata.value = self.rdata_next
        self.rdata_next = None
        await Timer(1, "ns")

        ic_req = bool(dut.ic_req.value)
        gnt = ic_req and not rst and not self.loading and self.wait_left == 0
        dut.ic_gnt.value = gnt
        await Timer(1, "ns")
        c = Cycle(
            i_valid=bool(dut.i_valid.value),
            i_rdata=int(dut.i_rdata.value),
            ic_req=ic_req,
            ic_gnt=gnt,
            ic_addr=int(dut.ic_addr.value),
        )

        if not rst:
            assert int(dut.ic_wstrb.value) == 0, "ic_wstrb must be 0000: the cache only reads"
            assert int(dut.ic_wdata.value) == 0, "ic_wdata must be 0"
            if c.ic_req and i_req:
                assert c.ic_addr == i_addr & 0x1FFC, (
                    f"ic_addr={c.ic_addr:#06x}, want {{i_addr[12:2], 2'b00}} = {i_addr & 0x1FFC:#06x}")
            if self.waiting:
                assert c.ic_req, "ic_req dropped before ic_gnt"
                assert c.ic_addr == self.last_ic_addr, "ic_addr changed before ic_gnt"

        # Bus model for the next cycle.
        self.waiting = c.ic_req and not gnt and not flush and not rst
        self.last_ic_addr = c.ic_addr
        if gnt:
            self.rdata_next = self.mem[(c.ic_addr >> 2) % self.words]
        if c.ic_req and not gnt and not self.loading:
            self.wait_left -= 1
        elif not c.ic_req or gnt:
            self.wait_left = self.bus_rng.randint(0, self.max_wait)
        return c

    async def fetch(self, addr):
        """Hold i_req high at addr until i_valid, as the core does, and check
        the word and the timing. Returns True if the fetch was a hit."""
        hit = self.model.access(addr)
        grant_at = []
        for waited in range(FETCH_TIMEOUT):
            c = await self.step(i_req=1, i_addr=addr)
            if c.i_valid:
                break
            if c.ic_gnt:
                grant_at.append(waited)
        else:
            assert False, f"fetch {addr:#06x}: no i_valid within {FETCH_TIMEOUT} cycles"

        want = self.mem[(addr >> 2) % self.words]
        assert c.i_rdata == want, (
            f"fetch {addr:#06x} ({'hit' if hit else 'miss'}): i_rdata={c.i_rdata:#010x}, want {want:#010x}")
        assert not c.ic_req, f"fetch {addr:#06x}: ic_req high in the cycle i_valid answers"
        if hit:
            assert waited == 0, (
                f"fetch {addr:#06x}: expected a hit, answered in the same cycle, but took {waited} cycles")
        else:
            assert waited > 0, f"fetch {addr:#06x}: expected a miss, but the cache answered at once"
            assert grant_at == [waited - 1], (
                f"fetch {addr:#06x}: a miss answers the cycle after its one grant; "
                f"grants in cycles {grant_at}, i_valid in cycle {waited}")
        self.fetches += 1
        self.hits += hit
        return hit

    async def idle(self, cycles=1):
        for _ in range(cycles):
            await self.step()

    async def flush(self, cycles=1):
        for _ in range(cycles):
            await self.step(flush=1)
        self.model.flush()


async def new_bench(dut):
    tb = Bench(dut)
    await tb.start()
    return tb


# Directed tests. Each checks an explicit hit/miss pattern as well, so a
# mistake in the model cannot hide a mistake in the cache.

@cocotb.test()
async def first_fetch_misses_then_hits(dut):
    """After reset the cache is empty; fetching the same word again hits."""
    tb = await new_bench(dut)
    assert not await tb.fetch(0x040)
    assert await tb.fetch(0x040)
    await tb.idle(2)
    assert await tb.fetch(0x040)


@cocotb.test()
async def sixteen_words_fill_every_line(dut):
    """16 consecutive words fill both ways of all 8 sets: a second pass all hits."""
    tb = await new_bench(dut)
    addrs = [0x100 + 4 * i for i in range(SETS * WAYS)]
    first = [await tb.fetch(a) for a in addrs]
    second = [await tb.fetch(a) for a in addrs]
    assert first == [False] * len(addrs)
    assert second == [True] * len(addrs)


@cocotb.test()
async def same_set_uses_both_ways(dut):
    """Two words with the same index and different tags stay cached together."""
    tb = await new_bench(dut)
    a, b = addr_of(3, 1), addr_of(3, 2)
    got = [await tb.fetch(x) for x in (a, b, a, b, a)]
    assert got == [False, False, True, True, True]


@cocotb.test()
async def lru_evicts_least_recently_used(dut):
    """A third tag in a full set replaces the way used least recently, and a
    hit counts as a use."""
    tb = await new_bench(dut)
    a, b, c = (addr_of(5, t) for t in (1, 2, 3))
    # c replaces a; b was used last, so a replaces c, then c replaces b.
    got = [await tb.fetch(x) for x in (a, b, c, b, a, c)]
    assert got == [False, False, False, True, False, False]

    a, b, c = (addr_of(6, t) for t in (1, 2, 3))
    # The hit on a makes b the victim.
    got = [await tb.fetch(x) for x in (a, b, a, c, a, b)]
    assert got == [False, False, True, False, True, False]


@cocotb.test()
async def flush_invalidates_every_line(dut):
    """One cycle of flush empties the cache."""
    tb = await new_bench(dut)
    addrs = [0x080 + 4 * i for i in range(SETS * WAYS)]
    for a in addrs:
        await tb.fetch(a)
    await tb.flush()
    assert [await tb.fetch(a) for a in addrs] == [False] * len(addrs)


@cocotb.test()
async def flush_beats_fill_in_same_cycle(dut):
    """flush has priority over a fill in the same cycle, so the line being
    filled is not kept."""
    tb = await new_bench(dut)
    old = 0x0C0
    await tb.fetch(old)
    new = 0x0A0
    for _ in range(FETCH_TIMEOUT):
        if (await tb.step(i_req=1, i_addr=new)).ic_gnt:
            break
    else:
        assert False, f"no ic_gnt for {new:#06x} within {FETCH_TIMEOUT} cycles"
    # The cycle after the grant is FILL: flush in it.
    await tb.step(i_req=1, i_addr=new, flush=1)
    await tb.idle()
    tb.model.flush()
    assert not await tb.fetch(new), "the line filled during flush survived it"
    assert not await tb.fetch(old), "a line cached before the flush survived it"


@cocotb.test()
async def reset_invalidates_every_line(dut):
    """rst empties the cache, like flush."""
    tb = await new_bench(dut)
    addrs = [0x180 + 4 * i for i in range(SETS * WAYS)]
    for a in addrs:
        await tb.fetch(a)
    await tb.reset()
    assert [await tb.fetch(a) for a in addrs] == [False] * len(addrs)


@cocotb.test()
async def reload_serves_only_the_new_program(dut):
    """A bootloader reload: flush is high and the bus grants nobody while new
    code is written to memory, and the core sits with i_req high, here in the
    middle of a miss. Afterwards every fetch must return the new code."""
    tb = await new_bench(dut)
    addrs = [4 * i for i in range(4 * SETS * WAYS)]  # more than fits: evictions too
    for a in addrs:
        await tb.fetch(a)

    tb.loading = True
    pending = addrs[-1] + 4  # not cached: its request waits for the bus
    for _ in range(3):
        await tb.step(i_req=1, i_addr=pending)
    for i in range(LOAD_CYCLES):
        await tb.step(i_req=1, i_addr=pending, flush=1)
        for w in range(i, tb.words, LOAD_CYCLES):
            tb.mem[w] ^= tb.rng.getrandbits(32) | 1  # every word changes
    tb.loading = False
    tb.model.flush()

    for a in [pending] + addrs:
        await tb.fetch(a)


# Random test, checked fetch by fetch against the model.

@cocotb.test()
async def random_fetch_stream(dut):
    """Program-like fetches: straight-line runs, loops, jumps back to recent
    code and fresh jumps, with idle gaps between fetches and an occasional
    flush. The bus delay is random on every request."""
    tb = await new_bench(dut)
    rng = tb.rng
    n = int(os.environ.get("FETCHES", "4000"))
    blocks = []
    while tb.fetches < n:
        if blocks and rng.random() < 0.5:
            start, length = rng.choice(blocks[-6:])
        else:
            start, length = rng.randrange(tb.span // 4) * 4, rng.randint(1, 12)
            blocks.append((start, length))
        for _ in range(rng.randint(1, 4)):
            for i in range(length):
                await tb.fetch((start + 4 * i) % tb.span)
                if rng.random() < 0.2:
                    await tb.idle(rng.randint(1, 3))
        if rng.random() < 0.02:
            await tb.flush()
    dut._log.info(f"{tb.fetches} fetches, {tb.hits} hits ({100 * tb.hits / tb.fetches:.0f}%)")


TRACE = os.environ.get("TRACE")


@cocotb.test(skip=not TRACE)
async def trace_replay(dut):
    """Replay the fetch addresses in TRACE=<file> and report the hit rate."""
    tb = await new_bench(dut)
    lines = Path(TRACE).read_text().split()
    addrs = [int(w, 16) for w in lines if not w.startswith(("//", "@"))]
    for a in addrs:
        await tb.fetch(a % tb.span)
    dut._log.info(f"{TRACE}: {tb.fetches} fetches, {tb.hits} hits")


@pytest.mark.parametrize("raw", [10, 11])
def test_icache(raw):
    """pytest entry point: build icache with Verilator for both RAM plans
    (RAW=10 and 11, as check_top.sh does) and run the cocotb tests above."""
    if TRACE:
        os.environ["TRACE"] = str(Path(TRACE).resolve())
    run("icache", ["rtl/cache/icache.sv"], "icache", "test_icache",
        parameters={"RAW": raw}, tag=f"_raw{raw}")
