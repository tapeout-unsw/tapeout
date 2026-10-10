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
from collections import Counter, namedtuple
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


def sample(signal, name, required=True):
    """Value of an output as an int. An X or Z is an error when `required` (a
    flip-flop that was never reset shows up this way under Icarus, which models
    X; Verilator only has 0 and 1); otherwise it reads as None."""
    value = signal.value
    if not value.is_resolvable:
        assert not required, f"{name} is {value}: not 0 or 1 (a register that was never reset?)"
        return None
    return int(value)


def hexs(value):
    return "X" if value is None else f"{value:#010x}"


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
        self.fill_next = False  # the next cycle is FILL (a grant, and no flush or rst)
        self.fetches = self.hits = 0
        self.cov = Counter()    # coverage: see fetch() and step()

    async def start(self, i_req=0, i_addr=0):
        d = self.dut
        # Drive every input before the first clock edge, so nothing is X.
        d.rst.value, d.flush.value, d.i_req.value, d.i_addr.value = 1, 0, i_req, i_addr
        d.ic_gnt.value, d.bus_rdata.value = 0, 0
        cocotb.start_soon(Clock(d.clk, CLK_NS, unit="ns").start())
        return await self.reset(i_req=i_req, i_addr=i_addr)

    async def reset(self, cycles=3, i_req=0, i_addr=0):
        """rst for `cycles` cycles, with i_req held as given (the core holds it
        high while in reset). Returns the cycles."""
        out = [await self.step(i_req=i_req, i_addr=i_addr, rst=1) for _ in range(cycles)]
        self.model.flush()
        return out

    async def step(self, i_req=0, i_addr=0, flush=0, rst=0):
        """Run one clock cycle with these inputs; return the outputs in it."""
        dut = self.dut
        await FallingEdge(dut.clk)
        in_fill, self.fill_next = self.fill_next, False
        if in_fill and flush:
            self.cov["flush_in_fill"] += 1
        if in_fill and rst:
            self.cov["rst_in_fill"] += 1
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

        # While rst is high the outputs may still be unknown (the first cycles
        # of a run); afterwards they must all be 0 or 1.
        ic_req = bool(sample(dut.ic_req, "ic_req", required=not rst))
        gnt = ic_req and not rst and not self.loading and self.wait_left == 0
        dut.ic_gnt.value = gnt
        await Timer(1, "ns")
        i_valid = bool(sample(dut.i_valid, "i_valid", required=not rst))
        c = Cycle(
            i_valid=i_valid,
            i_rdata=sample(dut.i_rdata, "i_rdata", required=False),  # checked when i_valid
            ic_req=ic_req,
            ic_gnt=gnt,
            ic_addr=sample(dut.ic_addr, "ic_addr", required=ic_req and not rst) or 0,
        )

        if not rst:
            assert sample(dut.ic_wstrb, "ic_wstrb") == 0, "ic_wstrb must be 0000: the cache only reads"
            assert sample(dut.ic_wdata, "ic_wdata") == 0, "ic_wdata must be 0"
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
            self.fill_next = not flush and not rst
        if c.ic_req and not gnt and not self.loading:
            self.wait_left -= 1
        elif not c.ic_req or gnt:
            self.wait_left = self.bus_rng.randint(0, self.max_wait)
        return c

    async def fetch(self, addr):
        """Hold i_req high at addr until i_valid, as the core does, and check
        the word and the timing. Returns True if the fetch was a hit."""
        index, tag = self.model.split(addr)
        ways = self.model.sets[index]
        if tag in ways:     # coverage, per set: hits on the newest or oldest line, misses
            self.cov[index, "hit_newest" if ways[0] == tag else "hit_oldest"] += 1
        else:
            self.cov[index, "evict" if len(ways) == WAYS else "miss_cold"] += 1
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
            f"fetch {addr:#06x} ({'hit' if hit else 'miss'}): i_rdata={hexs(c.i_rdata)}, want {want:#010x}")
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



# Edge cases. These drive the cache one cycle at a time (tb.step) to put it in
# the exact state named in each docstring: rst or flush in each state, bus
# extremes, and the cases where LRU state could go wrong.

async def run_to_grant(tb, addr):
    """Hold i_req at addr until the cycle with ic_gnt; the next cycle is FILL."""
    for _ in range(FETCH_TIMEOUT):
        if (await tb.step(i_req=1, i_addr=addr)).ic_gnt:
            return
    assert False, f"no ic_gnt for {addr:#06x} within {FETCH_TIMEOUT} cycles"


async def assert_quiet(tb, cycles=3, why=""):
    """With i_req low the cache must be idle: no bus request, no i_valid."""
    for _ in range(cycles):
        c = await tb.step()
        assert not c.ic_req and not c.i_valid, f"cache was not idle {why}"


@cocotb.test()
async def i_req_held_during_reset(dut):
    """The core holds i_req high while it is in reset. The cache must not ask
    the bus while rst is high, and must not keep a line from a fetch that was
    cut off: when rst is released the miss starts cleanly."""
    tb = await new_bench(dut)
    old, a = 0x0C0, 0x0A4
    await tb.fetch(old)
    await tb.step(i_req=1, i_addr=a, rst=1)   # first rst cycle: may still show the old state
    for _ in range(5):
        c = await tb.step(i_req=1, i_addr=a, rst=1)
        assert not c.ic_req, "ic_req while rst is high"
    tb.model.flush()
    assert not await tb.fetch(a), "a line appeared during reset"
    assert not await tb.fetch(old), "a line cached before the reset survived it"


@cocotb.test()
async def i_req_held_from_power_up(dut):
    """The same from the very first cycle of the run: i_req is high before the
    cache has ever been reset. No bus request while rst is high, and the
    cache starts empty."""
    tb = Bench(dut)
    a = 0x0A4
    cycles = await tb.start(i_req=1, i_addr=a)
    assert not any(c.ic_req for c in cycles[1:]), "ic_req while rst is high"
    assert not await tb.fetch(a)
    assert await tb.fetch(a)


@cocotb.test()
async def rst_in_the_middle_of_a_miss_and_in_fill(dut):
    """rst while waiting for the grant (1 and 3 cycles long), in the grant
    cycle, and in the FILL cycle: afterwards the cache is idle and empty, and
    the line that was being fetched is not kept."""
    tb = await new_bench(dut)
    old, a = addr_of(1, 1), addr_of(4, 2)
    for where in ("waiting", "grant cycle", "fill"):
        for rst_cycles in (1, 3):
            await tb.fetch(old)
            if where == "waiting":
                tb.loading = True
                for _ in range(4):
                    await tb.step(i_req=1, i_addr=a)
            elif where == "grant cycle":
                tb.max_wait, tb.wait_left = 0, 0
                await tb.step(i_req=1, i_addr=a)
            else:
                await run_to_grant(tb, a)
            await tb.reset(cycles=rst_cycles, i_req=1, i_addr=a)
            tb.loading = False
            tb.max_wait = 3
            await assert_quiet(tb, why=f"after rst in {where}")
            assert not await tb.fetch(a), f"the line from the miss was kept after rst in {where}"
            assert not await tb.fetch(old), f"a cached line survived rst in {where}"
            await tb.reset()


@cocotb.test()
async def flush_in_the_same_cycle_as_a_hit(dut):
    """flush while the cache is answering a hit: the line is gone afterwards,
    and so is every other line. Tried on both lines of a set."""
    tb = await new_bench(dut)
    x, y = addr_of(2, 1), addr_of(2, 2)
    for hit in (x, y):
        await tb.fetch(x)
        await tb.fetch(y)
        await tb.step(i_req=1, i_addr=hit, flush=1)
        tb.model.flush()
        await assert_quiet(tb, why="after flush on a hit")
        assert not await tb.fetch(hit), "the line hit during flush survived it"
        assert not await tb.fetch(y if hit == x else x), "the other line survived flush"


@cocotb.test()
async def flush_in_each_cycle_of_a_miss(dut):
    """flush while waiting for the grant (after 0 to 4 cycles) and in the grant
    cycle itself (FILL is covered by flush_beats_fill_in_same_cycle). Either
    way the cache is idle afterwards, nothing is kept, and the next fetch
    returns the right word, whether i_req stays high or is dropped first."""
    tb = await new_bench(dut)
    old, a = addr_of(1, 1), addr_of(5, 2)
    cases = [("waiting", n) for n in range(5)] + [("grant", 0)]
    for kind, n in cases:
        for hold in (True, False):
            await tb.fetch(old)
            await tb.step(i_req=1, i_addr=a)                 # IDLE: the miss is seen
            if kind == "waiting":
                tb.loading = True
                for _ in range(n):
                    await tb.step(i_req=1, i_addr=a)
                c = await tb.step(i_req=1, i_addr=a, flush=1)
                assert c.ic_req and not c.ic_gnt
                tb.loading = False
            else:
                tb.max_wait, tb.wait_left = 0, 0
                c = await tb.step(i_req=1, i_addr=a, flush=1)
                assert c.ic_req and c.ic_gnt, "this case needs flush in the grant cycle"
                tb.max_wait = 3
            tb.model.flush()
            if not hold:
                await assert_quiet(tb, why=f"after flush ({kind}, {n})")
            assert not await tb.fetch(a), f"the line was kept after flush ({kind}, {n})"
            assert not await tb.fetch(old), f"a cached line survived flush ({kind}, {n})"
            await tb.flush()


@cocotb.test()
async def miss_straight_after_a_fill_replaces_the_other_way(dut):
    """The cycle after a fill, a miss in the same set: the fill made its way
    the most recent, so the other way is the victim and the line just filled
    stays. Tried with each of the two old lines as the one left to evict."""
    tb = await new_bench(dut)
    x, y, a, b = (addr_of(3, t) for t in (1, 2, 3, 4))
    for used, other in ((x, y), (y, x)):
        await tb.flush()
        await tb.fetch(x)
        await tb.fetch(y)
        await tb.fetch(used)                    # `other` is now the victim
        assert not await tb.fetch(a)            # a replaces `other`
        assert not await tb.fetch(b)            # the very next fetch: b replaces `used`, not a
        assert await tb.fetch(a), "the line filled just before was replaced"
        assert await tb.fetch(b)
        assert not await tb.fetch(used)
        assert not await tb.fetch(other)


@cocotb.test()
async def refilling_after_a_flush_fills_both_ways_first(dut):
    """Flush clears the valid bits; the LRU bits may keep any value. Whatever
    they were, refilling a set fills both ways before anything is evicted:
    two new lines are both cached, in every set tried and after every kind
    of history, including a flush in FILL."""
    tb = await new_bench(dut)
    for index in (0, 3, 7):
        x, y, a, b, c = (addr_of(index, t) for t in (1, 2, 3, 4, 5))
        for history in ("x", "xy", "yx", "xyx", "xyy", "xyxy", "xyyy", "fill"):
            await tb.flush()
            if history == "fill":
                await tb.fetch(x)
                await run_to_grant(tb, y)
                await tb.step(i_req=1, i_addr=y, flush=1)       # flush in FILL
                tb.model.flush()
            else:
                await tb.fetch(x)
                if len(history) > 1:
                    await tb.fetch(y)
                for h in history[1:]:
                    await tb.fetch(x if h == "x" else y)
            await tb.flush()
            got = [await tb.fetch(t) for t in (a, b, a, b)]
            assert got == [False, False, True, True], \
                f"set {index}, history {history!r}: a way was evicted while the set still had room: {got}"
            assert not await tb.fetch(c) and await tb.fetch(b), \
                f"set {index}, history {history!r}: wrong victim after the refill"


async def stream(tb, count):
    """Program-like fetches over a small working set: runs, loops, jumps."""
    rng = tb.rng
    done = 0
    while done < count:
        start, length = rng.randrange(tb.span // 4) * 4, rng.randint(1, 10)
        for _ in range(rng.randint(1, 3)):
            for i in range(length):
                await tb.fetch((start + 4 * i) % tb.span)
                done += 1


@cocotb.test()
async def bus_that_always_grants_at_once(dut):
    """max_wait = 0: ic_gnt comes in the first cycle of ic_req. A miss then
    takes exactly three cycles (IDLE, MISS with the grant, FILL), and a long
    random stream still matches the model."""
    tb = await new_bench(dut)
    tb.max_wait = 0
    for t in (1, 2, 3):
        a = addr_of(2, t)
        c0 = await tb.step(i_req=1, i_addr=a)
        c1 = await tb.step(i_req=1, i_addr=a)
        c2 = await tb.step(i_req=1, i_addr=a)
        assert not c0.i_valid and not c0.ic_req
        assert c1.ic_req and c1.ic_gnt and not c1.i_valid
        assert c2.i_valid and not c2.ic_req
        tb.model.access(a)
    await stream(tb, int(os.environ.get("FETCHES", "4000")) // 4)


@cocotb.test()
async def bus_with_long_stalls(dut):
    """Stalls of 20 and 30 cycles with the request held (the Bench checks that
    ic_req and ic_addr stay put), then a stream with random stalls up to 20."""
    tb = await new_bench(dut)
    for stall, t in ((20, 1), (30, 2)):
        a = addr_of(6, t)
        await tb.step(i_req=1, i_addr=a)
        tb.loading = True
        for _ in range(stall):
            c = await tb.step(i_req=1, i_addr=a)
            assert c.ic_req and not c.i_valid
        tb.loading = False
        assert not await tb.fetch(a)
        assert await tb.fetch(a)
    tb.max_wait = 20
    await stream(tb, int(os.environ.get("FETCHES", "4000")) // 8)


@cocotb.test()
async def valid_bits_are_known_and_clear_after_reset(dut):
    """After reset no line is valid: with i_req high, no address in any set
    hits. Under Icarus (SIM=icarus) a valid bit that reset never touches
    shows up as X on i_valid and fails here; with RANDOM_INIT=1 Verilator
    starts it at a random value and a random hit shows up the same way.
    With neither, this only checks the clean case."""
    tb = await new_bench(dut)
    tb.loading = True           # no grants: a probe never fills anything
    for index in range(SETS):
        for tag in range(1 << tb.tag_bits):
            c = await tb.step(i_req=1, i_addr=addr_of(index, tag))
            assert not c.i_valid, f"set {index}, tag {tag}: a line is valid after reset"
            await tb.reset(cycles=1)    # back to IDLE for the next probe
    tb.loading = False


@cocotb.test()
async def coverage_report(dut):
    """Run a workload and report what it covered: for every set, a cold miss,
    a miss that evicts, a hit on the newest line and a hit on the oldest line
    of the set, plus a flush and an rst landing in a FILL cycle. The test
    fails if any of these never happened.

    Which physical way holds a line cannot be seen from the ports. The
    newest/oldest split stands in for it: the newest line is in the way that
    was filled or hit last, so across a run both ways take both roles."""
    tb = await new_bench(dut)
    for index in range(SETS):                         # one directed pass per set
        x, y, z = (addr_of(index, t) for t in (1, 2, 3))
        for a in (x, y, y, x, z):                     # cold, cold, newest, oldest, evict
            await tb.fetch(a)
    b = addr_of(0, 4)
    await run_to_grant(tb, b)
    await tb.step(i_req=1, i_addr=b, flush=1)         # flush in FILL
    tb.model.flush()
    await run_to_grant(tb, b)
    await tb.reset(cycles=1, i_req=1, i_addr=b)       # rst in FILL
    await stream(tb, int(os.environ.get("FETCHES", "4000")) // 4)

    kinds = ("miss_cold", "evict", "hit_newest", "hit_oldest")
    rows = [f"set  " + "  ".join(f"{k:>10}" for k in kinds)]
    missing = []
    for index in range(SETS):
        counts = [tb.cov[index, k] for k in kinds]
        rows.append(f"{index:>3}  " + "  ".join(f"{n:>10}" for n in counts))
        missing += [f"set {index} {k}" for k, n in zip(kinds, counts) if n == 0]
    rows.append(f"flush in FILL: {tb.cov['flush_in_fill']}, rst in FILL: {tb.cov['rst_in_fill']}")
    dut._log.info("coverage\n" + "\n".join(rows))
    missing += [k for k in ("flush_in_fill", "rst_in_fill") if tb.cov[k] == 0]
    assert not missing, f"never covered: {', '.join(missing)}"


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
