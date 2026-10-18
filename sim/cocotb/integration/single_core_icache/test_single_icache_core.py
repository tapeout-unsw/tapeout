"""Level 2: real core + real instruction cache, Python memory model.

C1 straight_line: instruction data, retirement order and a result store.
C2 branch_loop: repeated instructions hit; the program stores 12.
C3 delayed_memory: the same program with 1 and 5 denied grant cycles.
C4 conflict_replacement: three tags compete in one set; every fetch misses.
C5 reset_flush_restart: replace a program under reset/flush and execute it.

Scope: one core/cache pair, no real arbiter/SRAM and no two-core contention.
The data model supports full-word stores only; these programs issue no loads.
Cache prediction matches the supplied six-bit tags and fill-only FIFO policy.
All test instruction addresses are below 0x800, avoiding upper-address aliasing.
"""

from __future__ import annotations

import cocotb
from cocotb.triggers import Timer
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

# Tell Python where ref_model.py lives.
sys.path.insert(0, str(HERE.parent))

from ref_model import as_int, run_cocotb


def addi(rd, rs1, imm):
    assert -2048 <= imm < 2048
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (rd << 7) | 0x13


def sw(rs2, address):
    assert 0 <= address < 2048 and address % 4 == 0
    return (((address >> 5) & 0x7F) << 25) | (rs2 << 20) | (2 << 12) | ((address & 31) << 7) | 0x23


def jal(offset):
    assert offset % 2 == 0 and -(1 << 20) <= offset < (1 << 20)
    imm = offset & 0x1FFFFF
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) | 0x6F


LOOP = {
    0x00: addi(1, 0, 4),
    0x04: addi(2, 0, 0),
    0x08: addi(2, 2, 3),
    0x0C: addi(1, 1, -1),
    0x10: 0xFE009CE3,           # bne x1, x0, -8
    0x14: sw(2, 0x100),
    0x18: jal(0),
}
LOOP_PCS = [0, 4] + [8, 12, 16] * 4 + [20]


class Bench:
    """Manual 10 ns clock: drive low, check pre-edge, return data post-edge.

    Do not start a second clock task. Each step includes its accepting edge,
    so its observations cannot be counted twice or left uncommitted.
    """

    def __init__(self, dut):
        self.dut = dut

    async def reset(self, program, pcs, stores=(), delay=0):
        self.program = dict(program)       # BYTE-addressed instruction words
        self.expected_pcs = list(pcs)
        self.expected_stores = list(stores)
        self.delay = delay
        self.cycle = 0
        self.retired = []
        self.stores = []
        self.fetches = []
        self.reads = []
        self.active = None
        self.waiting_bus = None
        self.wait_cycles = 0
        self.lines = [[None, None] for _ in range(8)]
        self.victims = [0] * 8
        d = self.dut
        d.clk.value = 0
        d.rst.value = 1
        d.flush.value = 1
        d.ic_gnt.value = 0
        d.bus_rdata.value = 0
        d.d_gnt.value = 0
        d.d_rdata.value = 0
        for _ in range(3):
            await Timer(5, "ns")
            d.clk.value = 1
            await Timer(5, "ns")
            d.clk.value = 0
        d.rst.value = 0
        d.flush.value = 0

    async def step(self):
        d = self.dut
        await Timer(2, "ns")
        where = f"cycle {self.cycle}"
        req = as_int(d.ic_req)
        grant = False
        response = None

        assert as_int(d.ic_wstrb) == 0, f"{where}: instruction write"
        assert as_int(d.ic_wdata) == 0
        if req:
            address = as_int(d.ic_addr)
            assert address % 4 == 0
            if self.waiting_bus is None:
                self.waiting_bus, self.wait_cycles = address, 0
            assert address == self.waiting_bus, f"{where}: bus address changed before grant"
            if self.wait_cycles >= self.delay:
                assert address in self.program, f"{where}: unexpected bus read {address:#x}"
                grant, response = True, self.program[address]
                self.reads.append(address)
            else:
                self.wait_cycles += 1
        else:
            assert self.waiting_bus is None, f"{where}: request dropped before grant"
        d.ic_gnt.value = int(grant)
        d.d_gnt.value = as_int(d.d_req)
        await Timer(3, "ns")

        if self.active is not None:
            assert as_int(d.i_req), f"{where}: unfinished fetch dropped"
            assert as_int(d.i_addr) == self.active["pc"], f"{where}: stalled PC changed"
        if as_int(d.i_req):
            pc = as_int(d.i_addr)
            assert pc in self.program, f"{where}: unexpected fetch {pc:#x}"
            if self.active is None:
                index, tag = (pc >> 2) & 7, (pc >> 5) & 63
                self.active = dict(pc=pc, index=index, tag=tag,
                                   hit=tag in self.lines[index], start=self.cycle,
                                   grant=None)
            a = self.active
            if grant:
                assert not a["hit"], f"{where}: predicted hit requested memory"
                assert as_int(d.ic_addr) == pc
                assert a["grant"] is None, f"{where}: duplicate grant"
                a["grant"] = self.cycle
            valid = as_int(d.i_valid)
            due = a["hit"] or (a["grant"] is not None and self.cycle == a["grant"] + 1)
            assert bool(valid) == due, f"{where}: unexpected response timing at {pc:#x}"
            if valid:
                assert as_int(d.i_rdata) == self.program[pc], f"{where}: incorrect instruction at {pc:#x}"
                if a["hit"]:
                    assert self.cycle == a["start"], f"{where}: delayed cache hit"
                else:
                    index = a["index"]
                    victim = self.victims[index]
                    self.lines[index][victim] = a["tag"]
                    self.victims[index] = 1 - victim
                self.fetches.append((pc, a["hit"]))
                self.active = None
        else:
            assert not as_int(d.i_valid), f"{where}: unsolicited instruction"
            assert not req, f"{where}: memory request without core fetch"

        if as_int(d.retire):
            n = len(self.retired)
            pc = as_int(d.retire_pc)
            assert n < len(self.expected_pcs), f"{where}: extra retirement"
            assert pc == self.expected_pcs[n], f"{where}: retirement {n}: got {pc:#x}, expected {self.expected_pcs[n]:#x}"
            self.retired.append(pc)
        if as_int(d.d_req):
            store = (as_int(d.d_addr), as_int(d.d_wdata), as_int(d.d_wstrb))
            n = len(self.stores)
            assert n < len(self.expected_stores), f"{where}: unexpected data access {store}"
            assert store == self.expected_stores[n], f"{where}: wrong store {store}"
            self.stores.append(store)

        d.clk.value = 1
        await Timer(1, "ns")
        if grant:
            d.bus_rdata.value = response
            self.waiting_bus = None
        await Timer(4, "ns")
        d.clk.value = 0
        self.cycle += 1

    async def run(self, max_cycles=2000):
        for _ in range(max_cycles):
            await self.step()
            if len(self.retired) == len(self.expected_pcs):
                assert self.stores == self.expected_stores
                assert [pc for pc, _ in self.fetches] == self.expected_pcs
                self.dut._log.info("%d retired, %d hits, %d memory reads",
                                   len(self.retired), sum(hit for _, hit in self.fetches), len(self.reads))
                return
        raise AssertionError(f"Program did not finish after {max_cycles} cycles")


@cocotb.test()
async def straight_line(dut):
    tb = Bench(dut)
    program = {0: addi(1, 0, 5), 4: addi(2, 1, 7), 8: sw(2, 0x100), 12: jal(0)}
    await tb.reset(program, [0, 4, 8], [(0x100, 12, 15)])
    await tb.run()
    assert tb.reads == [0, 4, 8]


@cocotb.test()
async def branch_loop(dut):
    tb = Bench(dut)
    await tb.reset(LOOP, LOOP_PCS, [(0x100, 12, 15)])
    await tb.run()
    assert tb.reads == [0, 4, 8, 12, 16, 20]
    assert sum(hit for _, hit in tb.fetches) == 9


@cocotb.test()
async def delayed_memory(dut):
    tb = Bench(dut)
    for delay in (1, 5):
        await tb.reset(LOOP, LOOP_PCS, [(0x100, 12, 15)], delay=delay)
        await tb.run()
        assert tb.reads == [0, 4, 8, 12, 16, 20]


@cocotb.test()
async def conflict_replacement(dut):
    tb = Bench(dut)
    # Each word is a real jump; all three instruction addresses use set zero.
    program = {0x00: jal(32), 0x20: jal(32), 0x40: jal(-64)}
    pcs = [0x00, 0x20, 0x40] * 4
    await tb.reset(program, pcs, delay=2)
    await tb.run()
    assert tb.reads == pcs
    assert not any(hit for _, hit in tb.fetches)


@cocotb.test()
async def reset_flush_restart(dut):
    tb = Bench(dut)
    for result in (7, 19):
        program = {0: addi(1, 0, result), 4: sw(1, 0x100), 8: jal(0)}
        await tb.reset(program, [0, 4], [(0x100, result, 15)])
        await tb.run()
        assert tb.reads == [0, 4], "Old program survived reset/flush"


# ------------------------------------------------------------------ pytest

def test_single_core_icache():
    # Compile the supplied RTL tree so all decoder/ALU/register-file dependencies
    # are included without guessing their subdirectory names. Match your team's
    # explicit source list instead if the tree includes duplicate/stub modules.
    harness = HERE / "single_core_icache_harness.sv"

    sources = [
        *sorted((ROOT / "rtl/core").glob("*.sv")),
        *sorted((ROOT / "rtl/cache").glob("*.sv")),
        harness,
    ]

    assert harness.is_file(), f"Missing harness: {harness}"

    run_cocotb(toplevel="single_core_icache_harness",
               sources=sources,
               parameters={}, test_module="test_single_icache_core",
               tag="core_icache")
