"""Blackbox cocotb tests for the instruction cache's control FSM.

The FSM (IDLE, MISS, FILL) has no port of its own that the tests can see, so
they run on the whole `icache` and read the state off its outputs:

  IDLE  ic_req = 0, i_valid = i_req & hit      (the answer comes at once)
  MISS  ic_req = 1, i_valid = 0                (waiting for ic_gnt)
  FILL  ic_req = 0, i_valid = 1, i_rdata = bus_rdata

Unlike test_icache.py, which fetches whole words, these tests drive one clock
cycle at a time with explicit control of i_req, ic_gnt, flush and rst, and the
FsmBench below checks the outputs of EVERY cycle against a small model of the
header's behaviour (state, hit, data). A test is a directed sequence that puts
the FSM in the situation named in its docstring; the checking is the same for
all of them.

Checklist items (icache_FSM.sv):
  f1  IDLE stays IDLE with no i_req, or on a hit
  f2  IDLE -> MISS on i_req && !hit
  f3  MISS waits until ic_gnt, then goes to FILL
  f4  FILL -> IDLE after exactly one cycle
  f5  flush and rst return to IDLE from every state
  f6  ic_req is high only in MISS
  f7  the write enable is high only in FILL, never while flush is high
  f8  i_valid = (IDLE & i_req & hit) | FILL

f6 and f8 are checked on every cycle of every test. f7 cannot be seen at a
port; the tests check its effects: a stray write in IDLE would change a cached
word, and a write during flush would leave a line behind. A stray write in
MISS cannot be seen from outside, because the fill overwrites the same way a
few cycles later; that needs a look at the RTL or a unit test of the FSM.

Left unchecked because the header does not define them: i_valid and i_rdata in
a cycle where flush or rst is high, and every output while rst is high.

Run with `make test-cocotb BLOCK=icache_fsm`. SEED=<n> seeds the random test
(default 1); CYCLES=<n> sets its length.
"""

import os
import random
import re
import sys
from collections import namedtuple
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "icache"))
from common.runner import ROOT, run  # noqa: E402
from icache_model import SETS, ICacheModel  # noqa: E402

CLK_NS = 10
FETCH_TIMEOUT = 60

IDLE, MISS, FILL = "IDLE", "MISS", "FILL"

Cycle = namedtuple("Cycle", "i_valid i_rdata ic_req ic_gnt ic_addr")


def addr_of(index, tag):
    """Byte address of the word with this set index and tag."""
    return (tag << 5) | (index << 2)


class FsmBench:
    """Cycle-level driver for the core and bus sides, with a model that
    predicts every output.

    step() runs one clock cycle. Inputs change on the falling edge; ic_gnt is
    raised only if the cache is asking (ic_req) and not in reset, as a real
    arbiter would; bus_rdata carries the requested word only in the cycle
    after a grant, and noise otherwise, so a cache that samples it at the
    wrong time is caught. self.state is the model's state for the NEXT cycle.
    """

    def __init__(self, dut):
        self.dut = dut
        self.raw = int(dut.RAW.value)
        self.words = 1 << (self.raw - 2)
        seed = int(os.environ.get("SEED", "1"))
        dut._log.info(f"RAW={self.raw} SEED={seed} (replay with SEED={seed})")
        self.rng = random.Random(seed)
        self.noise = random.Random(seed + 1)
        self.mem = [self.rng.getrandbits(32) for _ in range(self.words)]
        self.cache = ICacheModel(self.raw - 5)
        self.state = IDLE
        self.rdata_next = None
        self.cycle = 0

    def word(self, addr):
        return self.mem[(addr >> 2) % self.words]

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_NS, unit="ns").start())
        await self.reset()

    async def reset(self):
        """Three cycles of rst: back to an empty cache in IDLE."""
        for _ in range(3):
            await self.step(rst=1)

    async def step(self, i_req=0, i_addr=0, flush=0, rst=0, gnt=0):
        """One clock cycle with these inputs; returns the outputs seen in it."""
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.rst.value = rst
        dut.flush.value = flush
        dut.i_req.value = i_req
        dut.i_addr.value = i_addr
        bus = self.noise.getrandbits(32) if self.rdata_next is None else self.rdata_next
        dut.bus_rdata.value = bus
        self.rdata_next = None
        await Timer(1, "ns")

        ic_req = int(dut.ic_req.value)
        granted = bool(gnt and ic_req and not rst)
        dut.ic_gnt.value = granted
        await Timer(1, "ns")
        c = Cycle(
            i_valid=bool(dut.i_valid.value),
            i_rdata=int(dut.i_rdata.value),
            ic_req=bool(ic_req),
            ic_gnt=granted,
            ic_addr=int(dut.ic_addr.value),
        )
        self.cycle += 1
        if not rst:
            self._check(c, i_req, i_addr, flush, bus)
        self._advance(c, i_req, i_addr, flush, rst)
        return c

    def _check(self, c, i_req, i_addr, flush, bus):
        where = f"cycle {self.cycle}, FSM should be in {self.state}"
        assert int(self.dut.ic_wstrb.value) == 0, f"{where}: ic_wstrb must be 0000"
        assert int(self.dut.ic_wdata.value) == 0, f"{where}: ic_wdata must be 0"

        if self.state == IDLE:
            hit = i_req and self.cache.is_hit(i_addr)
            assert not c.ic_req, f"{where}: ic_req is high outside MISS"
            if not flush:
                assert c.i_valid == hit, (
                    f"{where}: i_req={i_req}, {'hit' if hit else 'miss'} "
                    f"-> i_valid should be {int(hit)}, got {int(c.i_valid)}")
                if hit:
                    assert c.i_rdata == self.word(i_addr), (
                        f"{where}: hit data {c.i_rdata:#010x}, want {self.word(i_addr):#010x}")
        elif self.state == MISS:
            assert c.ic_req, f"{where}: ic_req dropped before ic_gnt"
            assert not c.i_valid, f"{where}: i_valid is high while waiting for the bus"
            if i_req:
                assert c.ic_addr == i_addr & 0x1FFC, (
                    f"{where}: ic_addr={c.ic_addr:#06x}, want {i_addr & 0x1FFC:#06x}")
        else:  # FILL
            assert not c.ic_req, f"{where}: ic_req is high outside MISS"
            if not flush:
                assert c.i_valid, f"{where}: i_valid should be high in FILL"
                assert c.i_rdata == bus, (
                    f"{where}: i_rdata={c.i_rdata:#010x}, want bus_rdata {bus:#010x}")

    def _advance(self, c, i_req, i_addr, flush, rst):
        if c.ic_gnt:
            self.rdata_next = self.word(c.ic_addr)
        if rst or flush:
            self.state = IDLE
            self.cache.flush()
        elif self.state == IDLE:
            if i_req:
                if self.cache.is_hit(i_addr):
                    self.cache.access(i_addr)   # a hit makes the line most recent
                else:
                    self.state = MISS
        elif self.state == MISS:
            if c.ic_gnt:
                self.state = FILL
        else:
            self.cache.access(i_addr)   # the fill makes the line most recent
            self.state = IDLE

    async def idle(self, n=1):
        for _ in range(n):
            await self.step()

    async def fetch(self, addr, wait=0):
        """Hold i_req at addr until i_valid, as the core does. In MISS the
        grant comes after `wait` cycles without it. Returns the cycles."""
        cycles = []
        waited = 0
        for _ in range(FETCH_TIMEOUT):
            in_miss = self.state == MISS
            c = await self.step(i_req=1, i_addr=addr, gnt=in_miss and waited >= wait)
            cycles.append(c)
            if c.i_valid:
                assert c.i_rdata == self.word(addr), f"fetch {addr:#06x}: wrong word"
                return cycles
            waited += in_miss
        assert False, f"fetch {addr:#06x}: no i_valid within {FETCH_TIMEOUT} cycles"


async def new_bench(dut):
    dut.rst.value = 0
    dut.flush.value = 0
    dut.i_req.value = 0
    dut.i_addr.value = 0
    dut.ic_gnt.value = 0
    dut.bus_rdata.value = 0
    tb = FsmBench(dut)
    await tb.start()
    return tb


# f1: IDLE stays IDLE

@cocotb.test()
async def idle_without_a_request_stays_idle(dut):
    """With i_req low the cache stays quiet, whatever i_addr does, even for an
    address that is not cached: no bus request, no i_valid."""
    tb = await new_bench(dut)
    for i in range(12):
        c = await tb.step(i_req=0, i_addr=addr_of(i % SETS, 1 + i % 3))
        assert not c.ic_req and not c.i_valid
    assert tb.state == IDLE


@cocotb.test()
async def idle_stays_idle_on_hits(dut):
    """A held i_req on a cached line answers in every cycle and never asks the
    bus; several different hits in a row do the same."""
    tb = await new_bench(dut)
    a, b = addr_of(2, 1), addr_of(5, 2)
    await tb.fetch(a)
    await tb.fetch(b)
    for _ in range(6):
        c = await tb.step(i_req=1, i_addr=a)
        assert c.i_valid and not c.ic_req
    for addr in (a, b, b, a, a, b):
        c = await tb.step(i_req=1, i_addr=addr)
        assert c.i_valid and not c.ic_req
    assert tb.state == IDLE


# f2: IDLE -> MISS

@cocotb.test()
async def miss_starts_the_cycle_after_the_request(dut):
    """i_req on an uncached address: the first cycle is IDLE (no i_valid, no
    ic_req); the next one is MISS and asks for the line."""
    tb = await new_bench(dut)
    a = addr_of(3, 2)
    first = await tb.step(i_req=1, i_addr=a)
    assert not first.i_valid and not first.ic_req, "the miss must not answer or ask in its first cycle"
    second = await tb.step(i_req=1, i_addr=a)
    assert second.ic_req and not second.i_valid
    assert second.ic_addr == a & 0x1FFC
    assert tb.state == MISS


@cocotb.test()
async def different_tag_in_a_cached_set_is_a_miss(dut):
    """A set that holds a line for tag 1 still misses on tag 2: the tag is
    compared, a valid bit alone is not a hit."""
    tb = await new_bench(dut)
    await tb.fetch(addr_of(6, 1))
    first = await tb.step(i_req=1, i_addr=addr_of(6, 2))
    assert not first.i_valid
    second = await tb.step(i_req=1, i_addr=addr_of(6, 2))
    assert second.ic_req


# f3: MISS waits for the grant

@cocotb.test()
async def miss_waits_for_the_grant(dut):
    """Held without a grant for 0, 1, 2, 5 and 20 cycles: ic_req and ic_addr
    stay put and i_valid stays low; one cycle after the grant the FSM is in
    FILL and answers with bus_rdata."""
    tb = await new_bench(dut)
    for n, wait in enumerate((0, 1, 2, 5, 20)):
        a = addr_of(n, 1)
        cycles = await tb.fetch(a, wait=wait)
        asked = [c for c in cycles if c.ic_req]
        assert len(asked) == wait + 1, f"wait={wait}: ic_req was high in {len(asked)} cycles"
        assert [c.ic_gnt for c in asked] == [False] * wait + [True]
        assert len({c.ic_addr for c in asked}) == 1, "ic_addr changed while waiting"
        assert cycles[-1].i_valid and not cycles[-1].ic_req   # the FILL cycle
        assert not cycles[-2].i_valid                         # the grant cycle
        await tb.idle()


@cocotb.test()
async def grant_cycle_does_not_answer(dut):
    """The cycle with ic_gnt is still MISS: no i_valid yet, the data is not on
    the bus until the next cycle."""
    tb = await new_bench(dut)
    a = addr_of(1, 3)
    await tb.step(i_req=1, i_addr=a)
    c = await tb.step(i_req=1, i_addr=a, gnt=1)
    assert c.ic_req and c.ic_gnt and not c.i_valid
    c = await tb.step(i_req=1, i_addr=a)
    assert c.i_valid and c.i_rdata == tb.word(a)


# f4: FILL lasts one cycle

@cocotb.test()
async def fill_lasts_exactly_one_cycle(dut):
    """After the FILL cycle the FSM is back in IDLE: with i_req dropped it is
    quiet at once (a second FILL cycle would raise i_valid again)."""
    tb = await new_bench(dut)
    a = addr_of(4, 1)
    await tb.fetch(a)
    for _ in range(4):
        c = await tb.step()
        assert not c.i_valid and not c.ic_req
    assert tb.state == IDLE


@cocotb.test()
async def line_is_a_hit_the_cycle_after_its_fill(dut):
    """With i_req held, the cycle after FILL answers from the new line, with
    the same word, and the cycles after that do the same."""
    tb = await new_bench(dut)
    a = addr_of(4, 2)
    cycles = await tb.fetch(a, wait=2)
    fill = cycles[-1]
    for _ in range(3):
        c = await tb.step(i_req=1, i_addr=a)
        assert c.i_valid and not c.ic_req and c.i_rdata == fill.i_rdata


@cocotb.test()
async def next_miss_follows_straight_after_a_fill(dut):
    """A new request in the cycle after FILL is handled from IDLE: its first
    cycle is IDLE (no ic_req), the next one asks the bus."""
    tb = await new_bench(dut)
    await tb.fetch(addr_of(0, 1))
    b = addr_of(7, 1)
    c = await tb.step(i_req=1, i_addr=b)
    assert not c.ic_req and not c.i_valid, "FSM was not back in IDLE after the FILL cycle"
    c = await tb.step(i_req=1, i_addr=b)
    assert c.ic_req


# f5: flush and rst from every state

SITUATIONS = ("IDLE hit", "MISS waiting", "MISS grant cycle", "FILL")


async def event_in(tb, kind, where, hold_req):
    """Put the FSM in the state `where`, raise flush or rst for one cycle, then
    carry on with i_req dropped, or held (the core does not know). Afterwards
    the earlier cached line and the line in flight must both be gone, and the
    cache must still work."""
    kept, a = addr_of(1, 1), addr_of(4, 2)
    await tb.fetch(kept)
    ev = {kind: 1}
    if where == "IDLE hit":
        await tb.fetch(a)
        await tb.step(i_req=1, i_addr=a, **ev)
    else:
        await tb.step(i_req=1, i_addr=a)                     # IDLE miss
        if where == "MISS waiting":
            await tb.step(i_req=1, i_addr=a)
            await tb.step(i_req=1, i_addr=a, **ev)
        elif where == "MISS grant cycle":
            await tb.step(i_req=1, i_addr=a, gnt=1, **ev)
        else:
            await tb.step(i_req=1, i_addr=a, gnt=1)
            await tb.step(i_req=1, i_addr=a, **ev)
    assert tb.state == IDLE

    if hold_req:
        # i_req still high: a clean restart as a miss (IDLE cycle, then MISS).
        c = await tb.step(i_req=1, i_addr=a)
        assert not c.i_valid and not c.ic_req, f"{kind} in {where}: no clean restart"
        c = await tb.step(i_req=1, i_addr=a)
        assert c.ic_req, f"{kind} in {where}: the request was not restarted"
        await tb.fetch(a, wait=1)
    else:
        for _ in range(3):
            c = await tb.step()
            assert not c.i_valid and not c.ic_req, f"{kind} in {where}: FSM did not return to IDLE"
        cycles = await tb.fetch(a)
        assert any(c.ic_req for c in cycles), f"{kind} in {where}: the line in flight was kept"

    cycles = await tb.fetch(kept, wait=1)
    assert any(c.ic_req for c in cycles), f"{kind} in {where}: a line cached before it survived"
    await tb.idle(2)


@cocotb.test()
async def flush_returns_to_idle_from_every_state(dut):
    """flush in IDLE (on a hit), in MISS (waiting, and in the grant cycle) and
    in FILL: the FSM is in IDLE the next cycle, no line is kept, and the
    cache still works."""
    tb = await new_bench(dut)
    for where in SITUATIONS:
        for hold_req in (False, True):
            await tb.reset()
            await event_in(tb, "flush", where, hold_req)


@cocotb.test()
async def rst_returns_to_idle_from_every_state(dut):
    """The same four situations with rst instead of flush."""
    tb = await new_bench(dut)
    for where in SITUATIONS:
        for hold_req in (False, True):
            await tb.reset()
            await event_in(tb, "rst", where, hold_req)


# f7: no write outside FILL, none during flush

@cocotb.test()
async def bus_noise_outside_fill_is_never_stored(dut):
    """bus_rdata carries noise whenever it is not the answer. Hits, a long
    wait in MISS in the same set, and idle cycles must not change a cached
    word: afterwards every line still returns its own word."""
    tb = await new_bench(dut)
    a, b = addr_of(2, 1), addr_of(2, 2)
    await tb.fetch(a)
    for _ in range(8):
        c = await tb.step(i_req=1, i_addr=a)
        assert c.i_valid and c.i_rdata == tb.word(a)
    await tb.fetch(b, wait=15)             # misses in the same set, long wait
    for addr in (a, b, a, b):
        c = await tb.step(i_req=1, i_addr=addr)
        assert c.i_valid and c.i_rdata == tb.word(addr)
    await tb.idle(5)


@cocotb.test()
async def no_write_in_a_cycle_with_flush(dut):
    """flush in FILL, then the fetch is repeated: the line must not have been
    written (it misses again), and flush in the cycle after a FILL also leaves
    nothing behind."""
    tb = await new_bench(dut)
    a = addr_of(5, 3)
    await tb.step(i_req=1, i_addr=a)
    await tb.step(i_req=1, i_addr=a, gnt=1)
    await tb.step(i_req=1, i_addr=a, flush=1)           # FILL with flush
    await tb.idle(2)
    cycles = await tb.fetch(a)
    assert any(c.ic_req for c in cycles), "the line filled during flush was kept"
    await tb.step(flush=1)                              # flush right after a fill
    await tb.idle()
    cycles = await tb.fetch(a)
    assert any(c.ic_req for c in cycles), "the line was kept through a flush"


# f6, f8: checked on every cycle above; here under random control

@cocotb.test()
async def random_cycles_follow_the_model(dut):
    """Random core and bus behaviour: requests held until answered, random
    grant delays, and occasional flush and rst (also while i_req is high).
    The FsmBench compares every cycle: ic_req only in MISS, i_valid as in the
    header, data, and the cache contents seen through hits and misses."""
    tb = await new_bench(dut)
    rng = tb.rng
    n = int(os.environ.get("CYCLES", "6000"))
    req, addr, recent = False, 0, []
    for _ in range(n):
        if not req and rng.random() < 0.6:
            if recent and rng.random() < 0.5:
                addr = rng.choice(recent)
            else:
                addr = addr_of(rng.randrange(SETS), rng.randint(1, 3))
                recent = (recent + [addr])[-6:]
            req = True
        flush = rng.random() < 0.04
        rst = rng.random() < 0.015
        c = await tb.step(i_req=req, i_addr=addr, flush=flush, rst=rst, gnt=rng.random() < 0.4)
        if c.i_valid and not flush and not rst:
            req = False


def cache_sources():
    """icache.sv, plus every other file in rtl/cache/ that defines a module
    (the storage and FSM, once icache.sv instantiates them)."""
    others = [p for p in sorted((ROOT / "rtl" / "cache").glob("*.sv"))
              if p.name != "icache.sv" and re.search(r"^\s*module\s", p.read_text(), re.M)]
    return ["rtl/cache/icache.sv"] + [str(p.relative_to(ROOT)) for p in others]


@pytest.mark.parametrize("raw", [10, 11])
def test_icache_fsm(raw):
    """pytest entry point: build icache with Verilator for both RAM plans
    (RAW=10 and 11) and run the cocotb tests above."""
    run("icache_fsm", cache_sources(), "icache", "test_icache_fsm",
        parameters={"RAW": raw}, tag=f"_raw{raw}")
