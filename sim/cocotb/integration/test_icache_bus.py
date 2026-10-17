"""Level 2, cache to bus: the real I-caches on the real shared bus.

DUT: icache_bus_harness.sv. Two real icache instances, the real arbiter, the
SRAM side of addr_decode (inline while addr_decode is a stub) and the real
sram_mem. The test plays both cores' instruction ports, core 0's data port
(which writes programs and adds traffic) and the bootloader.

Checked on EVERY cycle of every test:
    * at most one grant, and only to a requester that is asking
    * while loading, nobody but the bootloader is granted
    * a cache keeps ic_req high until it is granted (a flush may drop it)
Checked on EVERY fetch:
    * the word returned is the word in memory when the cache read it, or, on
      a hit, the copy the cache filled earlier
    * hit or miss as icache_model.py predicts: a hit answers in the cycle it
      is asked for; a miss answers exactly one cycle after its grant, and in
      that cycle the bus data belongs to this cache (data_src)
Checked on every data-port load: its word, and that the bus data is d0's.

    C1 single_miss_through_the_bus   one cache on a quiet bus: misses fill
                                     from the real SRAM, then hit
    C2 miss_while_data_port_is_busy  d0 loads every cycle while core 0 misses
                                     through a loop; nobody waits more than
                                     one cycle and no fill takes d0's data
    C3 both_caches_miss_together     both cores fetch at once, different code
                                     and then the same code; each cache gets
                                     its own words
    C4 store_then_fetch              a fetch after a store returns the new
                                     word, also when both start together
    C5 store_over_a_cached_line      a cached line keeps the old word until a
                                     reload (no self-modifying code)
    C6 reload_through_the_boot_port  the bootloader rewrites memory while a
                                     miss is in flight; afterwards both cores
                                     see only the new program
    C7 back_to_back_misses           misses with no gap, in one set and
                                     across sets, while d0 keeps the bus busy
    C8 random_soak                   both cores run program-like fetch streams
                                     with random d0 loads and stores and the
                                     occasional reload

Run with `python -m pytest -q sim/cocotb/integration/test_icache_bus.py`.
SEED=<n> replays a random run (the test prints the one it used); FETCHES=<n>
sets the length of C8 (default 3000 per core); WAVES=1 writes a waveform.
"""

from __future__ import annotations

import random
import sys
from collections import deque

import cocotb
import pytest
from cocotb.triggers import FallingEdge, Timer

from ref_model import HERE, ROOT, as_int, env_int, run_cocotb, start_clock

sys.path.insert(0, str(ROOT / "sim" / "cocotb" / "icache"))
from icache_model import ICacheModel  # noqa: E402

TAG_BITS = 6                        # 39-bit line: 1 valid + 6 tag + 32 data
SRC_D0, SRC_IC0, SRC_IC1 = 1, 3, 4  # b_src values from the arbiter
TIMEOUT = 100                       # cycles any one access may take


class Core:
    """One core's instruction port. Fetches a queue of byte addresses one at a
    time, holding i_req and i_addr until i_valid, as core.sv does in S_FETCH.
    None in the queue is an idle cycle. Every answer is kept in `results` as
    (address, word, hit)."""

    def __init__(self, bench: "Bench", n: int) -> None:
        self.bench, self.n = bench, n
        self.src = SRC_IC0 if n == 0 else SRC_IC1
        self.model = ICacheModel(TAG_BITS)
        self.copy: dict[int, int] = {}   # word address -> word this cache holds
        self.todo: deque = deque()
        self.addr = None                 # fetch in progress
        self.results: list[tuple[int, int, bool]] = []
        self.fetches = self.hits = self.max_wait = 0

    def fetch(self, *addrs: int) -> None:
        self.todo.extend(addrs)

    def busy(self) -> bool:
        return self.addr is not None or bool(self.todo)

    def drive(self) -> tuple[int, int]:
        """At the falling edge: this cycle's i_req and i_addr. No new fetch
        starts during a flush; one in progress keeps i_req high, as a core
        held in reset does."""
        if self.addr is None and self.todo and not self.bench.flush:
            addr = self.todo.popleft()
            if addr is not None:
                self.addr = addr
                self.hit = self.model.access(addr)
                self.start = self.bench.cycle
                self.grant = None
                self.req_cycles = 0
        return (1, self.addr) if self.addr is not None else (0, 0)

    def flushed(self) -> None:
        """A flush cycle: the cache empties, and a fetch in progress starts
        again as a miss once the flush is over."""
        self.model.flush()
        self.copy.clear()
        if self.addr is not None:
            self.model.access(self.addr)
            self.hit = False
            self.start = self.bench.cycle + 1
            self.grant = None
            self.req_cycles = 0

    def observe(self, req: int, gnt: int, where: str) -> None:
        """After the inputs settle: follow the fetch and check its answer."""
        if self.bench.flush:
            self.flushed()
            return
        if self.addr is None:
            return
        dut, bench = self.bench.dut, self.bench
        what = f"{where}: core {self.n} fetch {self.addr:#05x}"
        if gnt:
            assert self.grant is None, f"{what}: granted twice for one miss"
            assert not self.hit, f"{what}: expected a hit, but the cache went to the bus"
            self.grant = bench.cycle
            self.fill = bench.mem.get(self.addr >> 2)
            self.max_wait = max(self.max_wait, self.req_cycles)
        elif req:
            self.req_cycles += 1
        if not as_int(getattr(dut, f"i{self.n}_valid")):
            assert bench.cycle - self.start < TIMEOUT, f"{what}: no i_valid after {TIMEOUT} cycles"
            return

        word = as_int(getattr(dut, f"i{self.n}_rdata"))
        if self.hit:
            assert bench.cycle == self.start, f"{what}: expected a hit, answered after {bench.cycle - self.start} cycles"
            want = self.copy[self.addr >> 2]
        else:
            assert self.grant is not None, f"{what}: expected a miss, but the cache answered without the bus"
            assert bench.cycle == self.grant + 1, f"{what}: a miss answers the cycle after its grant (cycle {self.grant})"
            src = as_int(dut.data_src)
            assert src == self.src, f"{what}: the fill took bus data that belongs to b_src {src}"
            want = self.fill
            self.copy[self.addr >> 2] = want
        assert want is not None, f"{what}: fetched a word that was never written"
        assert word == want, f"{what}: i_rdata={word:#010x}, want {want:#010x}"
        self.results.append((self.addr, word, self.hit))
        self.fetches += 1
        self.hits += self.hit
        self.addr = None


class Bench:
    """Drives every input on the falling edge, then checks the cycle. One
    step() per clock, so a given SEED always replays the same run."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.raw = int(dut.RAW.value)
        self.words = 1 << (self.raw - 2)
        self.seed = env_int("SEED", 1)
        self.rng = random.Random(self.seed)
        dut._log.info(f"RAW={self.raw} SEED={self.seed} (replay with SEED={self.seed})")
        self.mem: dict[int, int] = {}    # word address -> word, as written
        self.cores = [Core(self, 0), Core(self, 1)]
        self.d0: deque = deque()         # data-port ops: (is_store, word address, data)
        self.d0_op = None
        self.d0_wait = self.d0_max_wait = self.d0_loads = 0
        self.d0_expect = None            # word a load put on the bus for this cycle
        self.boot: deque = deque()       # bootloader writes: (word address, data)
        self.loading = self.flush = 0    # soc_top drives both from core_run
        self.cycle = 0
        self.prev_req = {SRC_IC0: 0, SRC_IC1: 0}
        self.prev_gnt = {SRC_IC0: 0, SRC_IC1: 0}

    # ---------------- data port and bootloader ----------------
    def store(self, word: int, data: int) -> None:
        self.d0.append((True, word, data))

    def load(self, word: int) -> None:
        self.d0.append((False, word, 0))

    async def write_program(self, first: int, count: int) -> None:
        """Write random words to word addresses first .. first+count-1 through
        the data port, as a loaded program would be."""
        for w in range(first, first + count):
            self.store(w, self.rng.getrandbits(32))
        await self.run()

    async def reload(self, program: dict[int, int]) -> None:
        """A bootloader reload: loading and flush high while the bootloader
        writes `program` (word address -> word) one word per cycle, as
        bootloader.sv does, then both low again."""
        self.loading = self.flush = 1
        self.boot.extend(program.items())
        await self.step()                # at least one flush cycle
        while self.boot:
            await self.step()
        self.loading = self.flush = 0

    # ---------------- one clock cycle ----------------
    async def step(self, rst: int = 0) -> None:
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.rst.value = rst
        dut.loading.value = self.loading
        dut.flush.value = self.flush
        if self.loading and self.boot and not rst:
            word, data = self.boot.popleft()
            dut.boot_req.value, dut.boot_addr.value, dut.boot_wdata.value = 1, word << 2, data
            self.mem[word] = data            # the arbiter grants it this cycle
        else:
            dut.boot_req.value = dut.boot_addr.value = dut.boot_wdata.value = 0
        for core in self.cores:
            req, addr = core.drive() if not rst else (0, 0)
            getattr(dut, f"i{core.n}_req").value = req
            getattr(dut, f"i{core.n}_addr").value = addr
        if self.d0_op is None and self.d0 and not rst:
            self.d0_op, self.d0_wait = self.d0.popleft(), 0
        if self.d0_op is not None:
            is_store, word, data = self.d0_op
            dut.d0_req.value = 1
            dut.d0_addr.value = word << 2
            dut.d0_wdata.value = data if is_store else 0
            dut.d0_wstrb.value = 0b1111 if is_store else 0
        else:
            dut.d0_req.value = 0
            dut.d0_addr.value = dut.d0_wdata.value = dut.d0_wstrb.value = 0
        await Timer(1, "ns")
        if rst:
            self.cycle += 1
            return

        where = f"cycle {self.cycle}"
        req = {SRC_IC0: as_int(dut.ic0_req), SRC_IC1: as_int(dut.ic1_req)}
        gnt = {SRC_D0: as_int(dut.d0_gnt), SRC_IC0: as_int(dut.ic0_gnt), SRC_IC1: as_int(dut.ic1_gnt)}

        assert sum(gnt.values()) <= 1, f"{where}: more than one grant {gnt}"
        assert not (self.loading and any(gnt.values())), f"{where}: granted {gnt} while loading"
        assert not gnt[SRC_D0] or self.d0_op is not None, f"{where}: d0_gnt without d0_req"
        for s, name in ((SRC_IC0, "icache 0"), (SRC_IC1, "icache 1")):
            assert not gnt[s] or req[s], f"{where}: {name} granted without ic_req"
            if self.prev_req[s] and not self.prev_gnt[s] and not self.flush:
                assert req[s], f"{where}: {name} dropped ic_req before ic_gnt"

        # Data port: a load's word is on the bus the cycle after its grant.
        if self.d0_expect is not None:
            src, word = as_int(dut.data_src), as_int(dut.bus_rdata)
            assert src == SRC_D0, f"{where}: d0's load data is tagged b_src {src}"
            assert word == self.d0_expect, f"{where}: d0 load got {word:#010x}, want {self.d0_expect:#010x}"
            self.d0_expect = None
            self.d0_loads += 1
        if self.d0_op is not None:
            if gnt[SRC_D0]:
                is_store, word, data = self.d0_op
                if is_store:
                    self.mem[word] = data          # written at the edge ending this cycle
                else:
                    self.d0_expect = self.mem[word]
                self.d0_max_wait = max(self.d0_max_wait, self.d0_wait)
                self.d0_op = None
            elif not self.loading:                 # the bootloader owns the bus while loading
                assert self.d0_wait < TIMEOUT, f"{where}: d0 not granted in {TIMEOUT} cycles"
                self.d0_wait += 1

        for core in self.cores:
            core.observe(req[core.src], gnt[core.src], where)
        self.prev_req = {s: req[s] for s in (SRC_IC0, SRC_IC1)}
        self.prev_gnt = {s: gnt[s] for s in (SRC_IC0, SRC_IC1)}
        self.cycle += 1

    async def run(self, max_cycles: int = 200_000) -> None:
        """Step until every queue is empty and every access has finished."""
        for _ in range(max_cycles):
            if not (any(c.busy() for c in self.cores) or self.d0 or self.d0_op is not None
                    or self.d0_expect is not None):
                return
            await self.step()
        raise AssertionError(f"still busy after {max_cycles} cycles")


async def setup(dut) -> Bench:
    start_clock(dut.clk)
    tb = Bench(dut)
    for _ in range(3):
        await tb.step(rst=1)
    return tb


# ------------------------------------------------------------------ tests

@cocotb.test()
async def single_miss_through_the_bus(dut):
    """C1: one cache on a quiet bus. A program is written through the data
    port; core 0 fetches one word twice (miss, then hit), then 16 consecutive
    words twice: every line fills from the real SRAM, the second pass all
    hits, and with nobody else asking every miss is granted at once."""
    tb = await setup(dut)
    await tb.write_program(0, 128)
    core = tb.cores[0]

    core.fetch(0x040, 0x040)
    await tb.run()
    assert (core.fetches, core.hits) == (2, 1)

    words = [0x100 + 4 * i for i in range(16)]
    core.fetch(*words, *words)
    await tb.run()
    assert (core.fetches, core.hits) == (34, 17)
    assert core.max_wait == 0


@cocotb.test()
async def miss_while_data_port_is_busy(dut):
    """C2: core 0's data port loads every cycle while core 0's cache misses
    through a 24-instruction loop (more than the 16 lines, so it keeps
    missing). Round robin between two requesters means neither waits more than
    one cycle; every load gets its own word and no fill takes d0's data."""
    tb = await setup(dut)
    await tb.write_program(0, tb.words)
    core = tb.cores[0]

    loop = [0x080 + 4 * i for i in range(24)]
    core.fetch(*(loop * 4))
    for _ in range(300):
        tb.load(tb.rng.randrange(tb.words // 2, tb.words))
    await tb.run()

    assert core.fetches == len(loop) * 4 and tb.d0_loads == 300
    assert core.max_wait <= 1 and tb.d0_max_wait <= 1
    dut._log.info(f"{core.fetches - core.hits} misses went to the bus alongside {tb.d0_loads} loads")


@cocotb.test()
async def both_caches_miss_together(dut):
    """C3: both cores start fetching in the same cycle, first different code
    (core 0 low memory, core 1 the upper half), then the same code. The caches
    alternate on the bus, neither waits more than one cycle, and each fill
    takes the word meant for its own cache."""
    tb = await setup(dut)
    await tb.write_program(0, tb.words)
    c0, c1 = tb.cores
    half = tb.words * 2                      # byte address of the upper half

    a0 = [4 * i for i in range(16)]
    a1 = [half + 4 * i for i in range(16)]
    c0.fetch(*a0, *a0)
    c1.fetch(*a1, *a1)
    await tb.run()
    assert (c0.hits, c1.hits) == (16, 16)

    same = [0x0C0 + 4 * i for i in range(16)]
    c0.fetch(*same)
    c1.fetch(*same)
    await tb.run()
    assert c0.max_wait <= 1 and c1.max_wait <= 1


@cocotb.test()
async def store_then_fetch(dut):
    """C4: a word stored through the data port is what a later miss fetches.
    First in sequence, then with the store and the fetch starting in the same
    cycle, so the arbiter orders them; the fetch sees whichever word memory
    held when its own read was granted."""
    tb = await setup(dut)
    await tb.write_program(0, 64)
    core = tb.cores[0]

    new = 0x0BADC0DE
    tb.store(0x10, new)
    await tb.run()
    core.fetch(0x10 << 2)
    await tb.run()
    assert core.results[-1] == (0x040, new, False)

    for word in range(0x20, 0x28):           # store and fetch start together
        tb.store(word, tb.rng.getrandbits(32))
        core.fetch(word << 2)
        await tb.run()
    assert core.fetches == 9


@cocotb.test()
async def store_over_a_cached_line(dut):
    """C5: a store does not update a line already in the cache. The next hit
    still returns the old word; only a reload empties the cache, after which
    the fetch misses and returns the new word. This is why programs must not
    modify their own code."""
    tb = await setup(dut)
    await tb.write_program(0, 64)
    core = tb.cores[0]
    old = tb.mem[0x08]

    core.fetch(0x020)
    await tb.run()
    tb.store(0x08, old ^ 0xFFFF_FFFF)
    await tb.run()
    core.fetch(0x020)
    await tb.run()
    assert core.results[-1] == (0x020, old, True), "the cached copy should be the old word"

    await tb.reload({})
    core.fetch(0x020)
    await tb.run()
    assert core.results[-1] == (0x020, old ^ 0xFFFF_FFFF, False)


@cocotb.test()
async def reload_through_the_boot_port(dut):
    """C6: both caches hold program 1 when the bootloader starts. Core 0 has
    a miss in flight that cycle. The bootloader writes program 2 over the same
    addresses through the real boot port while loading and flush are high;
    nobody else is granted meanwhile. Afterwards the in-flight fetch and every
    later fetch, on both cores, return program 2."""
    tb = await setup(dut)
    await tb.write_program(0, 64)
    c0, c1 = tb.cores
    code = [4 * i for i in range(16)]
    c0.fetch(*code)
    c1.fetch(*code)
    await tb.run()

    program2 = {w: tb.rng.getrandbits(32) for w in range(64)}
    c0.fetch(0x0C0)                          # not cached: a miss starts now
    await tb.step()
    await tb.reload(program2)
    c0.fetch(*code)
    c1.fetch(*code)
    await tb.run()

    for core in (c0, c1):
        tail = core.results[-len(code):]
        assert all(word == program2[addr >> 2] and not hit for addr, word, hit in tail)
    assert c0.results[-len(code) - 1] == (0x0C0, program2[0x30], False)


@cocotb.test()
async def back_to_back_misses(dut):
    """C7: misses with no gap between them while d0 loads every cycle, so a
    new request is already on the bus while fill data comes back. Three tags
    in one set evict each other (the oldest fill goes), then eight sets fill
    in a row on both cores at once."""
    tb = await setup(dut)
    await tb.write_program(0, tb.words)
    c0, c1 = tb.cores
    for _ in range(200):
        tb.load(tb.rng.randrange(tb.words))

    same_set = [0x000, 0x020, 0x040, 0x000]  # tags 0, 1, 2, then 0 again
    c0.fetch(*same_set)
    await tb.run()
    assert [hit for _, _, hit in c0.results] == [False, False, False, False]

    for _ in range(200):
        tb.load(tb.rng.randrange(tb.words))
    row = [0x100 + 4 * i for i in range(8)]
    c0.fetch(*row, *row)
    c1.fetch(*row, *row)
    await tb.run()
    assert c0.hits == 8 and c1.hits == 8
    assert max(c0.max_wait, c1.max_wait, tb.d0_max_wait) <= 2


@cocotb.test(timeout_time=200, timeout_unit="ms")
async def random_soak(dut):
    """C8: both cores fetch program-like streams (straight runs, loops, jumps
    back to recent code, fresh jumps, idle gaps) while d0 loads and stores at
    random, now and then over code a cache holds. Every so often the
    bootloader reloads part of memory. Every fetch and load is checked."""
    tb = await setup(dut)
    rng = tb.rng
    await tb.write_program(0, tb.words)
    n = env_int("FETCHES", 3000)

    def stream(count: int) -> list:
        """`count` fetch addresses, with idle gaps (None) between some."""
        out, blocks, fetches = [], [], 0
        while fetches < count:
            if blocks and rng.random() < 0.5:
                start, length = rng.choice(blocks[-6:])
            else:
                start, length = rng.randrange(tb.words) * 4, rng.randint(1, 12)
                blocks.append((start, length))
            for _ in range(rng.randint(1, 4)):
                for i in range(length):
                    out.append((start + 4 * i) % (tb.words * 4))
                    fetches += 1
                    if rng.random() < 0.15:
                        out.extend([None] * rng.randint(1, 3))
        return out

    c0, c1 = tb.cores
    s0, s1 = stream(n), stream(n)
    chunk = 500
    for i in range(0, max(len(s0), len(s1)), chunk):
        c0.fetch(*s0[i:i + chunk])
        c1.fetch(*s1[i:i + chunk])
        for _ in range(chunk // 3):
            w = rng.randrange(tb.words)
            if rng.random() < 0.3:
                tb.store(w, rng.getrandbits(32))
            else:
                tb.load(w)
        await tb.run()
        if rng.random() < 0.5:
            first = rng.randrange(tb.words - 32)
            await tb.reload({w: rng.getrandbits(32) for w in range(first, first + 32)})

    for core in (c0, c1):
        dut._log.info(f"core {core.n}: {core.fetches} fetches, {core.hits} hits "
                      f"({100 * core.hits // max(core.fetches, 1)}%), longest wait {core.max_wait}")
    dut._log.info(f"d0: {tb.d0_loads} loads checked, longest wait {tb.d0_max_wait}")


# ------------------------------------------------------------------ pytest

@pytest.mark.parametrize("raw", [10, 11])
def test_icache_bus(raw):
    cache = ROOT / "rtl" / "cache"
    run_cocotb(toplevel="icache_bus_harness",
               sources=[ROOT / "rtl/bus/arbiter.sv",
                        cache / "icache.sv", cache / "icache_FSM.sv",
                        cache / "icache_storage.sv", cache / "icache_lru.sv",
                        HERE / "icache_bus_harness.sv"],
               parameters={"RAW": raw}, test_module="test_icache_bus",
               tag=f"icache_bus_raw{raw}")
