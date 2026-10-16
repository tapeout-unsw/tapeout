"""Level 2, cache to bus: the real I-caches on the real shared bus.

DUT: icache_bus_harness.sv. Two real icache instances, the real arbiter, the
SRAM side of addr_decode (inline while addr_decode is a stub) and the real
sram_mem. The test plays both cores' instruction ports and core 0's data
port, which writes the programs and adds traffic.

Checked on EVERY cycle of every test:
    * at most one grant, and only to a requester that is asking
    * a cache keeps ic_req high until it is granted
Checked on EVERY fetch:
    * the word returned is the word in memory when the cache read it
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

Run with `python -m pytest -q sim/cocotb/integration/test_icache_bus.py`.
SEED=<n> replays a random run; WAVES=1 writes a waveform.
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
    None in the queue is an idle cycle."""

    def __init__(self, bench: "Bench", n: int) -> None:
        self.bench, self.n = bench, n
        self.src = SRC_IC0 if n == 0 else SRC_IC1
        self.model = ICacheModel(TAG_BITS)
        self.copy: dict[int, int] = {}   # word address -> word this cache holds
        self.todo: deque = deque()
        self.addr = None                 # fetch in progress
        self.fetches = self.hits = self.max_wait = 0

    def fetch(self, *addrs: int) -> None:
        self.todo.extend(addrs)

    def busy(self) -> bool:
        return self.addr is not None or bool(self.todo)

    def drive(self) -> tuple[int, int]:
        """At the falling edge: this cycle's i_req and i_addr."""
        if self.addr is None and self.todo:
            addr = self.todo.popleft()
            if addr is not None:
                self.addr = addr
                self.hit = self.model.access(addr)
                self.start = self.bench.cycle
                self.grant = None
                self.req_cycles = 0
        return (1, self.addr) if self.addr is not None else (0, 0)

    def observe(self, req: int, gnt: int, where: str) -> None:
        """After the inputs settle: follow the fetch and check its answer."""
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
        self.cycle = 0
        self.prev_req = {SRC_IC0: 0, SRC_IC1: 0}
        self.prev_gnt = {SRC_IC0: 0, SRC_IC1: 0}

    # ---------------- data port ----------------
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

    # ---------------- one clock cycle ----------------
    async def step(self, rst: int = 0) -> None:
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.rst.value = rst
        dut.loading.value = 0
        dut.flush.value = 0
        dut.boot_req.value = 0
        dut.boot_addr.value = 0
        dut.boot_wdata.value = 0
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
        assert not gnt[SRC_D0] or self.d0_op is not None, f"{where}: d0_gnt without d0_req"
        for s, name in ((SRC_IC0, "icache 0"), (SRC_IC1, "icache 1")):
            assert not gnt[s] or req[s], f"{where}: {name} granted without ic_req"
            if self.prev_req[s] and not self.prev_gnt[s]:
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
            else:
                assert self.d0_wait < TIMEOUT, f"{where}: d0 not granted in {TIMEOUT} cycles"
                self.d0_wait += 1

        for core in self.cores:
            core.observe(req[core.src], gnt[core.src], where)
        self.prev_req = {s: req[s] for s in (SRC_IC0, SRC_IC1)}
        self.prev_gnt = {s: gnt[s] for s in (SRC_IC0, SRC_IC1)}
        self.cycle += 1

    async def run(self, max_cycles: int = 100_000) -> None:
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
