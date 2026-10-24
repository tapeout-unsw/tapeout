"""cocotb unit tests for rtl/cache/dcache_FSM.sv.

dcache_FSM is the controller of the data cache: it decides what each core data
access does (answer from the cache, go to the bus, or both over two cycles) and
tells the datapath what to do. These tests drive its ports directly, one clock
cycle at a time. In every cycle the inputs change on the falling edge and the
outputs are read 1 ns later, before the next rising edge, so they show the
combinational outputs for the state the FSM is in.

Directed tests, one per line of the table in the header of dcache_FSM.sv:
  load hit, load miss (waiting, grant, FILL), killed fill, stores (hit and
  miss), MMIO and dcache_off pass-through, flush and rst from every state,
  a request straight after FILL.
A random test then checks every cycle against a model that is written as a
table of the header's rules, and checks some invariants that must hold whatever
the inputs are (for example, MMIO is never installed).

Run with `make test-cocotb BLOCK=dcache_fsm`. SEED=<n> sets the seed of the
random test (default 1); CYCLES=<n> its length.
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

CLK_NS = 10
INPUTS = ("rst", "flush", "dcache_off", "d_req", "d_mmio", "d_store",
          "cache_hit", "dc_gnt", "kill_fill")
OUTPUTS = ("d_gnt", "dc_req", "serve_hit", "miss_start", "store_hit",
           "in_fill", "fill", "resp_bus")

IDLE, FILL, PASS = "IDLE", "FILL", "PASS"


class Out(dict):
    """The outputs of one cycle: out.d_gnt, out.fill, ..."""
    __getattr__ = dict.__getitem__

    def only(self, *high):
        """True if exactly the named outputs are high."""
        return {k for k, v in self.items() if v} == set(high)


def expected(state, i):
    """What the header of dcache_FSM.sv says: the outputs for this state and
    these inputs, and the state of the next cycle. i is a dict of the inputs."""
    out = dict.fromkeys(OUTPUTS, 0)
    nxt = IDLE
    if i["rst"]:
        return out, IDLE            # outputs are unspecified during rst; not compared
    if i["flush"]:
        return out, IDLE            # flush drops every output
    bypass = i["dcache_off"] or i["d_mmio"]
    if state == IDLE and i["d_req"]:
        gnt = i["dc_gnt"]
        if bypass:
            out.update(dc_req=1, d_gnt=gnt)
            nxt = PASS if gnt and not i["d_store"] else IDLE
        elif i["d_store"]:
            out.update(dc_req=1, d_gnt=gnt, store_hit=int(gnt and i["cache_hit"]))
        elif i["cache_hit"]:
            out.update(d_gnt=1, serve_hit=1)
        else:
            out.update(dc_req=1, d_gnt=gnt, miss_start=gnt)
            nxt = FILL if gnt else IDLE
    elif state == FILL:
        out.update(in_fill=1, fill=int(not i["kill_fill"]), resp_bus=1)
    elif state == PASS:
        out.update(resp_bus=1)
    return out, nxt


class Bench:
    """Drives dcache_FSM and follows it with the model above, so that every
    cycle of every test is also compared to the header's rules."""

    def __init__(self, dut):
        self.dut = dut
        self.state = IDLE
        self.cycle = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_NS, unit="ns").start())
        for n in INPUTS:
            getattr(self.dut, n).value = 0
        self.dut.rst.value = 1
        for _ in range(2):
            await FallingEdge(self.dut.clk)
        self.state = IDLE

    async def cyc(self, **inputs):
        """One clock cycle with these inputs (the rest low); returns the outputs."""
        i = {n: int(inputs.pop(n, 0)) for n in INPUTS}
        assert not inputs, f"unknown inputs {list(inputs)}"
        await FallingEdge(self.dut.clk)
        for n, v in i.items():
            getattr(self.dut, n).value = v
        await Timer(1, "ns")
        out = Out({n: int(getattr(self.dut, n).value) for n in OUTPUTS})
        self.cycle += 1
        if not i["rst"]:
            want, _ = expected(self.state, i)
            assert out == want, (
                f"cycle {self.cycle}, state {self.state}, inputs "
                f"{ {k: v for k, v in i.items() if v} }:\n"
                f"  got  {sorted(k for k, v in out.items() if v)}\n"
                f"  want {sorted(k for k, v in want.items() if v)}")
        _, self.state = expected(self.state, i)
        return out


async def new_bench(dut):
    tb = Bench(dut)
    await tb.start()
    return tb


async def quiet(tb, n=2):
    """Idle cycles with no request: every output must be low."""
    for _ in range(n):
        out = await tb.cyc()
        assert out.only(), "FSM is not idle"


# Loads

@cocotb.test()
async def load_hit_answers_at_once(dut):
    """A load that hits is granted in the same cycle, with no bus request. The
    data comes from the cache's register next cycle, not from bus_rdata."""
    tb = await new_bench(dut)
    out = await tb.cyc(d_req=1, cache_hit=1)
    assert out.only("d_gnt", "serve_hit")
    out = await tb.cyc()
    assert not out.resp_bus, "the hit's data must not come from bus_rdata"
    assert out.only()
    for _ in range(3):      # back to back hits
        out = await tb.cyc(d_req=1, cache_hit=1, dc_gnt=1)  # a grant on the bus is not ours
        assert out.only("d_gnt", "serve_hit")


@cocotb.test()
async def load_miss_waits_then_fills(dut):
    """A load miss goes to the bus and waits as long as the arbiter takes. The
    grant cycle passes d_gnt to the core and saves the miss; the next cycle is
    FILL: bus_rdata goes to the core and into the cache."""
    tb = await new_bench(dut)
    for _ in range(4):
        out = await tb.cyc(d_req=1)
        assert out.only("dc_req"), "waiting: dc_req only"
    out = await tb.cyc(d_req=1, dc_gnt=1)
    assert out.only("dc_req", "d_gnt", "miss_start")
    out = await tb.cyc()                               # FILL; the core has left S_MEM
    assert out.only("in_fill", "fill", "resp_bus")
    out = await tb.cyc()
    assert out.only(), "FILL must last exactly one cycle"


@cocotb.test()
async def killed_fill_is_not_installed(dut):
    """kill_fill (from the snoop) in the FILL cycle: the core still gets the
    data (resp_bus), but nothing is written (fill low). In any other cycle
    kill_fill does nothing."""
    tb = await new_bench(dut)
    await tb.cyc(d_req=1, kill_fill=1)                  # waiting
    out = await tb.cyc(d_req=1, dc_gnt=1, kill_fill=1)  # grant cycle
    assert out.only("dc_req", "d_gnt", "miss_start")
    out = await tb.cyc(kill_fill=1)                     # FILL
    assert out.only("in_fill", "resp_bus"), "a killed fill must still answer, but not install"
    out = await tb.cyc(kill_fill=1)                     # IDLE
    assert out.only()
    # The next miss is clean.
    await tb.cyc(d_req=1)
    await tb.cyc(d_req=1, dc_gnt=1)
    out = await tb.cyc()
    assert out.only("in_fill", "fill", "resp_bus")


@cocotb.test()
async def request_right_after_fill_is_handled(dut):
    """The core cannot ask again for at least 3 cycles, but the FSM is back in
    IDLE one cycle after FILL: a request then is handled as normal."""
    tb = await new_bench(dut)
    await tb.cyc(d_req=1)
    await tb.cyc(d_req=1, dc_gnt=1)
    await tb.cyc()                                      # FILL
    out = await tb.cyc(d_req=1, cache_hit=1)
    assert out.only("d_gnt", "serve_hit")
    out = await tb.cyc(d_req=1)
    assert out.only("dc_req")


# Stores

@cocotb.test()
async def store_is_write_through(dut):
    """A store always goes to the bus, hit or miss. The line is updated
    (store_hit) only in the grant cycle and only if it is cached. A store miss
    does not allocate: no miss_start, no FILL."""
    tb = await new_bench(dut)
    for hit in (0, 1):
        for _ in range(3):
            out = await tb.cyc(d_req=1, d_store=1, cache_hit=hit)
            assert out.only("dc_req"), "waiting store: only dc_req"
        out = await tb.cyc(d_req=1, d_store=1, cache_hit=hit, dc_gnt=1)
        if hit:
            assert out.only("dc_req", "d_gnt", "store_hit")
        else:
            assert out.only("dc_req", "d_gnt"), "a store miss must not allocate"
        await quiet(tb)                                 # no FILL, no response phase


# MMIO and the chicken bit

@cocotb.test()
async def mmio_passes_through_and_is_never_cached(dut):
    """MMIO accesses go to the bus whatever the lookup says. A load takes
    bus_rdata next cycle (PASS), and nothing is installed or updated."""
    tb = await new_bench(dut)
    for hit in (0, 1):
        out = await tb.cyc(d_req=1, d_mmio=1, cache_hit=hit)
        assert out.only("dc_req"), "MMIO load, waiting"
        out = await tb.cyc(d_req=1, d_mmio=1, cache_hit=hit, dc_gnt=1)
        assert out.only("dc_req", "d_gnt"), "no serve_hit, no miss_start for MMIO"
        out = await tb.cyc()
        assert out.only("resp_bus"), "PASS: the core takes bus_rdata, nothing is installed"
        await quiet(tb)

        out = await tb.cyc(d_req=1, d_mmio=1, d_store=1, cache_hit=hit)
        assert out.only("dc_req")
        out = await tb.cyc(d_req=1, d_mmio=1, d_store=1, cache_hit=hit, dc_gnt=1)
        assert out.only("dc_req", "d_gnt"), "an MMIO store never updates a line"
        await quiet(tb)


@cocotb.test()
async def dcache_off_passes_everything_through(dut):
    """With the chicken bit set a cacheable address behaves like MMIO, even on a
    hit: the system behaves as if there were no cache."""
    tb = await new_bench(dut)
    for store in (0, 1):
        out = await tb.cyc(d_req=1, d_store=store, cache_hit=1, dcache_off=1)
        assert out.only("dc_req")
        out = await tb.cyc(d_req=1, d_store=store, cache_hit=1, dcache_off=1, dc_gnt=1)
        assert out.only("dc_req", "d_gnt")
        if store:
            await quiet(tb)
        else:
            out = await tb.cyc(dcache_off=1)
            assert out.only("resp_bus")
            await quiet(tb)


# flush and rst

@cocotb.test()
async def flush_returns_to_idle_from_every_state(dut):
    """flush drops every output and the FSM is in IDLE the next cycle: from
    IDLE (with a request in flight), from the grant cycle, from FILL and from
    PASS. A flush in the grant cycle means no FILL follows."""
    tb = await new_bench(dut)

    out = await tb.cyc(d_req=1, cache_hit=1, flush=1)       # IDLE, would be a hit
    assert out.only()
    await quiet(tb)

    await tb.cyc(d_req=1)                                   # waiting
    out = await tb.cyc(d_req=1, flush=1)
    assert out.only()
    await quiet(tb)

    await tb.cyc(d_req=1)
    out = await tb.cyc(d_req=1, dc_gnt=1, flush=1)          # grant cycle
    assert out.only(), "flush in the grant cycle: no grant, no miss_start"
    await quiet(tb)                                         # and no FILL afterwards

    await tb.cyc(d_req=1)
    await tb.cyc(d_req=1, dc_gnt=1)
    out = await tb.cyc(flush=1)                             # FILL
    assert out.only(), "flush beats the fill"
    await quiet(tb)

    await tb.cyc(d_req=1, d_mmio=1, dc_gnt=1)
    out = await tb.cyc(flush=1)                             # PASS
    assert out.only()
    await quiet(tb)


@cocotb.test()
async def rst_returns_to_idle_from_every_state(dut):
    """rst from FILL and from PASS: the FSM is in IDLE the next cycle."""
    tb = await new_bench(dut)
    await tb.cyc(d_req=1)
    await tb.cyc(d_req=1, dc_gnt=1)
    await tb.cyc(rst=1)                                     # FILL
    await quiet(tb)
    await tb.cyc(d_req=1, d_mmio=1, dc_gnt=1)
    await tb.cyc(rst=1)                                     # PASS
    await quiet(tb)
    await tb.cyc(d_req=1)
    await tb.cyc(rst=1, d_req=1)                            # waiting, request held
    out = await tb.cyc(d_req=1)
    assert out.only("dc_req"), "a held request restarts cleanly after rst"


# Random

@cocotb.test()
async def random_inputs_follow_the_model(dut):
    """Random values on every input (the FSM has no memory of the core, so it
    must behave from any input in any state). Every cycle is compared to the
    model, and these hold whatever the inputs:
      d_gnt only with d_req; dc_req only with d_req; at most one of serve_hit,
      miss_start, store_hit; fill only with in_fill; MMIO and dcache_off never
      give serve_hit, miss_start or fill; a store never gives serve_hit,
      miss_start or fill; nothing at all is high during flush."""
    seed = int(os.environ.get("SEED", "1"))
    dut._log.info(f"SEED={seed} (replay with SEED={seed})")
    rng = random.Random(seed)
    tb = await new_bench(dut)
    weights = dict(rst=0.02, flush=0.05, dcache_off=0.1, d_req=0.7, d_mmio=0.2,
                   d_store=0.3, cache_hit=0.5, dc_gnt=0.5, kill_fill=0.3)
    for _ in range(int(os.environ.get("CYCLES", "6000"))):
        i = {n: rng.random() < p for n, p in weights.items()}
        out = await tb.cyc(**i)
        if i["rst"]:
            continue
        assert not out.d_gnt or i["d_req"] or out.resp_bus is False
        assert not (out.dc_req and not i["d_req"])
        assert out.serve_hit + out.miss_start + out.store_hit <= 1
        assert not out.fill or out.in_fill
        if i["flush"]:
            assert out.only()
        if (i["d_mmio"] or i["dcache_off"] or i["d_store"]) and not out.in_fill:
            assert not (out.serve_hit or out.miss_start or out.fill)
        if (i["d_mmio"] or i["dcache_off"]) and not out.in_fill:
            assert not out.store_hit


def test_dcache_fsm():
    """pytest entry point: build dcache_FSM with Verilator and run the cocotb
    tests above."""
    run("dcache_fsm", ["rtl/cache/dcache_FSM.sv"], "dcache_FSM", "test_dcache_fsm")
