"""Blackbox cocotb tests for the instruction cache's LRU bits.

The LRU bits (one per set, naming the victim way) have no module of their own
and are not visible at any port, so these tests run on the whole `icache` and
look at what the LRU bit decides: which line a miss in a full set throws away.
That shows up as a hit or a miss on a later fetch. Each test fills a set, uses
it in a chosen order, fetches a third tag and then asks "which of the first
two is still cached?".

Checklist items:
  l1  a hit on way 0 makes way 1 the victim, and vice versa
  l2  a fill makes the filled way the most recent
  l3  each set's bit is independent of the others

Which physical way a line lands in is not visible from outside, so l1 fills
each pair in both orders: whichever way the first fill picks, both ways get
their turn as the one that is hit.

The Bench (core and bus drivers, protocol checks) comes from
sim/cocotb/icache/test_icache.py; every fetch is also checked for data and
timing there. Run with `make test-cocotb BLOCK=icache_lru`. SEED=<n> seeds the
random test.
"""

import os
import re
import sys
from pathlib import Path

import cocotb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "icache"))
from common.runner import ROOT, run  # noqa: E402
from icache_model import SETS, WAYS  # noqa: E402
from test_icache import addr_of, new_bench  # noqa: E402

HIT, MISS = True, False


async def pattern(tb, addrs):
    """Fetch each address in order; return the list of hit (True) / miss."""
    return [await tb.fetch(a) for a in addrs]


# l1: hits choose the victim

@cocotb.test()
async def hit_makes_the_other_way_the_victim(dut):
    """Fill a and b, hit one of them, then bring in c: c must replace the one
    that was not just used. Done for both lines as the one hit, and for both
    fill orders, so each way is hit and each way is evicted."""
    tb = await new_bench(dut)
    index = 2
    for first, second in ((1, 2), (2, 1)):
        a, b, c = (addr_of(index, t) for t in (first, second, 3))
        for used, other in ((a, b), (b, a)):
            await tb.flush()
            got = await pattern(tb, [a, b, used, c])
            assert got == [MISS, MISS, HIT, MISS]
            # c replaced `other`: `used` is still there, `other` is gone.
            assert await tb.fetch(used) == HIT, \
                f"tags {first},{second}: the line that was just hit was evicted"
            assert await tb.fetch(other) == MISS, \
                f"tags {first},{second}: the line that was not hit survived"


@cocotb.test()
async def repeated_hits_on_one_way_keep_the_other_the_victim(dut):
    """Many hits on the same line change nothing: the other line stays the
    victim, and a hit on the other line then flips it."""
    tb = await new_bench(dut)
    a, b, c, d = (addr_of(4, t) for t in (1, 2, 3, 4))
    got = await pattern(tb, [a, b] + [a] * 5 + [c])
    assert got == [MISS, MISS] + [HIT] * 5 + [MISS]   # c replaced b
    assert await tb.fetch(a) == HIT
    # Now a, c are cached and a is the most recent. Hit c: a becomes the victim.
    assert await tb.fetch(c) == HIT
    assert await tb.fetch(d) == MISS                  # d replaced a
    assert await tb.fetch(c) == HIT
    assert await tb.fetch(a) == MISS


@cocotb.test()
async def alternating_hits_follow_the_last_use(dut):
    """Alternate hits between the two lines; the victim is always the one used
    least recently, however many times they swap."""
    tb = await new_bench(dut)
    a, b, c = (addr_of(6, t) for t in (1, 2, 3))
    for uses in ("ab", "ba", "aba", "abab", "baba", "abbba", "aabb"):
        await tb.flush()
        await pattern(tb, [a, b])                       # fill
        line = {"a": a, "b": b}
        got = await pattern(tb, [line[u] for u in uses])
        assert got == [HIT] * len(uses), f"uses {uses!r}: a line went missing"
        last = line[uses[-1]]
        other = b if last == a else a
        assert await tb.fetch(c) == MISS
        assert await tb.fetch(last) == HIT, \
            f"uses {uses!r}: the most recently used line was evicted"
        assert await tb.fetch(other) == MISS, \
            f"uses {uses!r}: the least recently used line survived"


# l2: a fill is a use

@cocotb.test()
async def fill_makes_the_new_line_most_recent(dut):
    """After filling a then b, b is the most recent, so c replaces a (not b)."""
    tb = await new_bench(dut)
    a, b, c = (addr_of(1, t) for t in (1, 2, 3))
    got = await pattern(tb, [a, b, c, b])
    assert got == [MISS, MISS, MISS, HIT], "c replaced b, the line filled just before it"
    assert await tb.fetch(a) == MISS


@cocotb.test()
async def fill_after_a_hit_makes_the_filled_way_most_recent(dut):
    """Fill a, b; hit a so b is the victim; fill c into b's way. c is now the
    most recent, so the next miss d replaces a, not c."""
    tb = await new_bench(dut)
    a, b, c, d = (addr_of(3, t) for t in (1, 2, 3, 4))
    got = await pattern(tb, [a, b, a, c, d])
    assert got == [MISS, MISS, HIT, MISS, MISS]
    assert await tb.fetch(c) == HIT, "the line filled just before d was evicted"
    assert await tb.fetch(a) == MISS, "a was not the victim after c's fill"


@cocotb.test()
async def every_fill_in_a_stream_evicts_the_oldest(dut):
    """A stream of new tags through one set: each miss evicts the line that was
    used (or filled) least recently, so only the last two tags are cached."""
    tb = await new_bench(dut)
    tags = [1, 2, 3, 4, 5, 6, 7]
    addrs = [addr_of(7, t) for t in tags]
    got = await pattern(tb, addrs)
    assert got == [MISS] * len(addrs)
    # Only the last two survive. Probe newest first so the probes do not evict them.
    assert await tb.fetch(addrs[-1]) == HIT
    assert await tb.fetch(addrs[-2]) == HIT
    assert await tb.fetch(addrs[-3]) == MISS


# l3: each set has its own bit

@cocotb.test()
async def sets_keep_independent_victims(dut):
    """In every set fill a, b, then hit a in even sets and b in odd sets, so
    the sets want opposite victims. A third tag in each set must then replace
    the right line in that set."""
    tb = await new_bench(dut)
    sets = range(SETS)
    a = {s: addr_of(s, 1) for s in sets}
    b = {s: addr_of(s, 2) for s in sets}
    c = {s: addr_of(s, 3) for s in sets}
    used = {s: a[s] if s % 2 == 0 else b[s] for s in sets}
    other = {s: b[s] if s % 2 == 0 else a[s] for s in sets}

    assert await pattern(tb, [x for s in sets for x in (a[s], b[s])]) == [MISS] * (2 * SETS)
    assert await pattern(tb, [used[s] for s in sets]) == [HIT] * SETS
    assert await pattern(tb, [c[s] for s in sets]) == [MISS] * SETS
    for s in sets:
        assert await tb.fetch(used[s]) == HIT, f"set {s}: the line that was hit was evicted"
    for s in sets:
        assert await tb.fetch(other[s]) == MISS, f"set {s}: the line that was not hit survived"


@cocotb.test()
async def traffic_in_other_sets_does_not_move_a_victim(dut):
    """Set 5 holds a (older) and b (newer). Heavy use of every other set,
    including hits and evictions, must not change that: c still replaces a."""
    tb = await new_bench(dut)
    a, b, c = (addr_of(5, t) for t in (1, 2, 3))
    await pattern(tb, [a, b])
    for s in range(SETS):
        if s == 5:
            continue
        x, y, z = (addr_of(s, t) for t in (4, 5, 6))
        await pattern(tb, [x, y, x, y, y, z, x, z])
    got = await pattern(tb, [c, b])
    assert got == [MISS, HIT], "traffic in other sets changed set 5's victim"
    assert await tb.fetch(a) == MISS


@cocotb.test()
async def interleaved_sets_follow_a_per_set_lru(dut):
    """Random fetches over three tags in every set, interleaved across sets.
    Each set behaves as its own 2-way LRU (the model checks hit or miss and
    the data on every fetch)."""
    tb = await new_bench(dut)
    rng = tb.rng
    n = int(os.environ.get("FETCHES", "1500"))
    for _ in range(n):
        await tb.fetch(addr_of(rng.randrange(SETS), rng.randint(1, 3)))
        if rng.random() < 0.15:
            await tb.idle(rng.randint(1, 3))
    dut._log.info(f"{tb.fetches} fetches, {tb.hits} hits ({100 * tb.hits / tb.fetches:.0f}%)")


def cache_sources():
    """icache.sv, plus every other file in rtl/cache/ that defines a module
    (the storage and FSM, once icache.sv instantiates them)."""
    others = [p for p in sorted((ROOT / "rtl" / "cache").glob("*.sv"))
              if p.name != "icache.sv" and re.search(r"^\s*module\s", p.read_text(), re.M)]
    return ["rtl/cache/icache.sv"] + [str(p.relative_to(ROOT)) for p in others]


@pytest.mark.parametrize("raw", [10, 11])
def test_icache_lru(raw):
    """pytest entry point: build icache with Verilator for both RAM plans
    (RAW=10 and 11) and run the cocotb tests above."""
    run("icache_lru", cache_sources(), "icache", "test_icache_lru",
        parameters={"RAW": raw}, tag=f"_raw{raw}")
