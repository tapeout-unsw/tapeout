"""Directed cocotb tests for rtl/cache/dcache.sv.

The testbench plays core 0 (one access at a time, like core.sv), the arbiter
and memory, and other bus sources writing to RAM. Every load is checked
against memory as it was when the load was accepted, and each test checks
which accesses hit (d_gnt without dc_req).

Run with `make test-cocotb BLOCK=dcache`. WAVES=1 writes a waveform.
"""

import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

SRC = 1     # this cache's b_src


def addr_of(index, tag):
    return (tag << 5) | (index << 2)


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.raw = int(dut.RAW.value)
        self.words = 1 << (self.raw - 2)
        self.rng = random.Random(1)
        self.mem = [self.rng.getrandbits(32) for _ in range(self.words)]
        self.mmio = {}
        self.rdata_next = None      # bus_rdata next cycle, after a granted read
        self.pending = None         # (addr, word) of a load accepted last cycle

    def read(self, addr):
        if addr & 0x1000:
            return self.mmio.get(addr & 0x1FFC, 0)
        return self.mem[(addr >> 2) % self.words]

    def write(self, addr, wstrb, wdata):
        m = sum(0xFF << (8 * b) for b in range(4) if wstrb >> b & 1)
        new = (self.read(addr) & ~m) | (wdata & m)
        if addr & 0x1000:
            self.mmio[addr & 0x1FFC] = new
        else:
            self.mem[(addr >> 2) % self.words] = new

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, 10, unit="ns").start())
        for _ in range(3):
            await self.step(rst=1)

    async def step(self, req=0, addr=0, wstrb=0, wdata=0, flush=0, rst=0, remote=None):
        """One cycle. This cache is granted whenever it asks, unless remote =
        (src, addr, wstrb, wdata) is granted instead. Returns (d_gnt, dc_req)."""
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.rst.value = rst
        dut.flush.value = flush
        dut.off.value = 0
        dut.d_req.value = req
        dut.d_addr.value = addr
        dut.d_wdata.value = wdata
        dut.d_wstrb.value = wstrb
        dut.bus_rdata.value = self.rng.getrandbits(32) if self.rdata_next is None else self.rdata_next
        self.rdata_next = None
        dut.dc_gnt.value = 0
        dut.b_valid.value = 0
        await Timer(1, "ns")

        dc_req = bool(dut.dc_req.value)
        ours = dc_req and remote is None and not rst
        if ours:
            dut.dc_gnt.value = 1
            dut.b_valid.value, dut.b_addr.value, dut.b_wstrb.value, dut.b_src.value = 1, addr, wstrb, SRC
        elif remote is not None:
            src, raddr, rwstrb, _ = remote
            dut.b_valid.value, dut.b_addr.value, dut.b_wstrb.value, dut.b_src.value = 1, raddr, rwstrb, src
        await Timer(1, "ns")
        d_gnt = bool(dut.d_gnt.value)

        if self.pending is not None and not rst:
            paddr, want = self.pending
            got = int(dut.d_rdata.value)
            assert got == want, f"load {paddr:#06x} returned {got:#010x}, memory held {want:#010x}"
        self.pending = (addr, self.read(addr)) if req and d_gnt and not wstrb else None

        if ours:
            if wstrb:
                self.write(addr, wstrb, wdata)
            else:
                self.rdata_next = self.read(addr)
        elif remote is not None:
            src, raddr, rwstrb, rwdata = remote
            if rwstrb:
                self.write(raddr, rwstrb, rwdata)
            else:
                self.rdata_next = self.read(raddr)
        return d_gnt, dc_req

    async def access(self, addr, wstrb=0, wdata=0):
        """Hold the request until d_gnt; a load then spends a cycle with d_req
        low while its data returns. Returns True if it was a hit."""
        for _ in range(10):
            d_gnt, dc_req = await self.step(req=1, addr=addr, wstrb=wstrb, wdata=wdata)
            if d_gnt:
                break
        else:
            assert False, f"{addr:#06x}: no d_gnt"
        if not wstrb:
            await self.step()
        return not dc_req

    async def load(self, addr):
        return await self.access(addr)

    async def store(self, addr, wdata, wstrb=0b1111):
        return await self.access(addr, wstrb, wdata)


async def new_bench(dut):
    tb = Bench(dut)
    await tb.start()
    return tb


@cocotb.test()
async def load_miss_then_hit(dut):
    tb = await new_bench(dut)
    assert not await tb.load(0x044)
    assert await tb.load(0x044)


@cocotb.test()
async def both_ways_then_lru(dut):
    """Two tags share a set; a third evicts the least recently used one."""
    tb = await new_bench(dut)
    a, b, c = (addr_of(5, t) for t in (0, 1, 2))
    got = [await tb.load(x) for x in (a, b, a, c, a, b)]
    assert got == [False, False, True, False, True, False]


@cocotb.test()
async def store_hit_merges_bytes(dut):
    tb = await new_bench(dut)
    a = addr_of(2, 1)
    await tb.load(a)
    await tb.store(a, 0xAAAAAAAA, 0b0001)
    await tb.store(a, 0xCCCCCCCC, 0b1100)
    assert await tb.load(a)


@cocotb.test()
async def store_miss_does_not_allocate(dut):
    tb = await new_bench(dut)
    a = addr_of(4, 2)
    await tb.store(a, 0x12345678)
    assert not await tb.load(a)


@cocotb.test()
async def remote_write_invalidates(dut):
    """A write by core 1 or the bootloader drops the line; reads and writes to
    other lines don't."""
    tb = await new_bench(dut)
    a = addr_of(1, 0)
    for src in (2, 0):
        await tb.load(a)
        await tb.step(remote=(src, a, 0b0010, 0x5500))
        assert not await tb.load(a)
    await tb.step(remote=(3, a, 0, 0))
    await tb.step(remote=(2, addr_of(1, 1), 0b1111, 7))
    await tb.step(remote=(2, 0x1000 | (a & 0x1F), 0b1111, 7))
    assert await tb.load(a)


@cocotb.test()
async def mmio_is_never_cached(dut):
    tb = await new_bench(dut)
    assert not await tb.load(0x1008)
    assert not await tb.load(0x1008)


@cocotb.test()
async def fill_race(dut):
    """Miss granted in cycle N, core 1's store to the same word in N+1: the
    load returns the old word and the old word is not installed."""
    tb = await new_bench(dut)
    a = addr_of(3, 2)
    d_gnt, dc_req = await tb.step(req=1, addr=a)
    assert d_gnt and dc_req
    await tb.step(remote=(2, a, 0b1111, 0xDEADBEEF))
    assert not await tb.load(a)


@cocotb.test()
async def flush_clears(dut):
    tb = await new_bench(dut)
    await tb.load(0x010)
    await tb.step(flush=1)
    assert not await tb.load(0x010)


@pytest.mark.parametrize("raw", [10, 11])
def test_dcache(raw):
    sources = [
        "rtl/cache/dcache.sv",
        "rtl/cache/dcache_storage.sv",
        "rtl/cache/icache_lru.sv",
    ]
    run("dcache", sources, "dcache", "test_dcache",
        parameters={"RAW": raw}, tag=f"_raw{raw}")
