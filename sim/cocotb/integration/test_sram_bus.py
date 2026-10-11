"""The SRAM on the shared bus, with one core's data port and its I-cache.

DUT: sram_bus_harness.sv (real arbiter → SRAM side of addr_decode → sram_mem).

Checked on EVERY cycle of every test:
    * at most one grant, and only to a master that is requesting
    * if anyone is requesting, someone is granted (the bus never idles while a
      master waits)
    * a read's data is on bus_rdata at the edge after its grant, tagged with
      the master that asked for it, and matches the reference memory
    * that data is still on the bus during the following cycle, while the
      next request is already travelling to the SRAM (the pipelined overlap)

    B1 alternating_back_to_back       both masters request every cycle: strict
                                      alternation, one access per cycle
    B2 store_then_fetch_next_cycle    a store is visible to a fetch the very
                                      next cycle
    B3 fetch_then_store_same_word     a fetch granted first returns the old
                                      word; a later fetch the new one
    B4 only_the_winner_reaches_sram   a waiting master's inputs, idle cycles
                                      and MMIO-space stores never change SRAM
    B5 core_timed_program             traffic shaped like one multi-cycle
                                      core: program loaded, then fetch,
                                      execute, load or store
    B6 random_contention              random traffic and gaps from both
                                      masters; nobody waits more than a cycle
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

import cocotb
import pytest
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from ref_model import HERE, MASK32, ROOT, ByteMemory, as_int, env_int, run_cocotb, start_clock

MMIO_BIT = 1 << 12                     # b_addr[12] selects MMIO, not SRAM
SRC_D0, SRC_IC0 = 1, 3                 # b_src values from the arbiter
CORE_STROBES = (0b0001, 0b0010, 0b0100, 0b1000, 0b0011, 0b1100, 0b1111)


@dataclass
class Op:
    write: bool
    addr: int                          # byte address, word aligned
    data: int = 0
    strb: int = 0b1111


class Master:
    """One bus master. Like core.sv and the I-cache, it holds its request
    and fields steady until it is granted."""

    def __init__(self, dut, prefix: str, name: str, src_id: int, can_write: bool) -> None:
        self.name, self.src_id, self.can_write = name, src_id, can_write
        self.req = getattr(dut, f"{prefix}_req")
        self.addr = getattr(dut, f"{prefix}_addr")
        self.gnt = getattr(dut, f"{prefix}_gnt")
        self.wdata = getattr(dut, f"{prefix}_wdata") if can_write else None
        self.wstrb = getattr(dut, f"{prefix}_wstrb") if can_write else None
        self.queue: deque[Op] = deque()
        self.current: Op | None = None
        self.ready_at = 0              # first cycle the next op may be presented
        self.gap = (0, 0)              # random quiet cycles after each grant
        self.waited = 0
        self.max_wait = 0
        self.junk_when_idle = False    # drive random fields while not requesting
        self.reads: list[tuple[Op, int]] = []

    def drive(self, cycle: int) -> None:
        if self.current is None and self.queue and cycle >= self.ready_at:
            self.current = self.queue.popleft()
            self.waited = 0
        op = self.current
        if op is None:
            self.req.value = 0
            if self.junk_when_idle:
                self.addr.value = random.getrandbits(13)
                if self.can_write:
                    self.wdata.value = random.getrandbits(32)
                    self.wstrb.value = random.getrandbits(4)
            return
        self.req.value = 1
        self.addr.value = op.addr
        if self.can_write:
            self.wdata.value = (op.data & MASK32) if op.write else random.getrandbits(32)
            self.wstrb.value = op.strb if op.write else 0


class SingleCoreBus:
    """Clocks the harness, plays both masters and checks every cycle."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.raw = env_int("SRAM_RAW", 11)
        self.words = 1 << (self.raw - 2)
        self.ref = ByteMemory(self.words * 4)
        self.d0 = Master(dut, "d0", "core0 D-port", SRC_D0, can_write=True)
        self.ic0 = Master(dut, "ic0", "core0 I-cache", SRC_IC0, can_write=False)
        self.masters = (self.d0, self.ic0)
        self.cycle = 0
        self.grants: list[str | None] = []
        self.busy_cycles = 0

    def word_addr(self, w: int) -> int:
        return (w % self.words) * 4

    async def reset(self) -> None:
        for name in ("d0_req", "d0_addr", "d0_wdata", "d0_wstrb", "ic0_req", "ic0_addr"):
            getattr(self.dut, name).value = 0
        self.dut.rst.value = 1
        start_clock(self.dut.clk)
        await ClockCycles(self.dut.clk, 3)
        await FallingEdge(self.dut.clk)
        self.dut.rst.value = 0
        await ClockCycles(self.dut.clk, 5)          # CEN high before the first access

    async def run(self, max_cycles: int = 200_000) -> None:
        """Clock the bus until every queued op has been granted and checked."""
        prev: tuple[Master, int] | None = None      # read data that should be on the bus
        start = self.cycle
        while any(m.queue or m.current for m in self.masters):
            await FallingEdge(self.dut.clk)
            for m in self.masters:
                m.drive(self.cycle)
            await ReadOnly()

            if prev is not None:
                assert as_int(self.dut.bus_rdata) == prev[1], (
                    f"cycle {self.cycle}: {prev[0].name}'s read data left the bus "
                    f"before the next access reached the SRAM")
            requesting = [m for m in self.masters if m.current is not None]
            granted = [m for m in self.masters if as_int(m.gnt)]
            assert len(granted) <= 1, f"cycle {self.cycle}: two grants in one cycle"
            assert all(m in requesting for m in granted), (
                f"cycle {self.cycle}: {granted[0].name} granted without requesting")
            assert bool(granted) == bool(requesting), (
                f"cycle {self.cycle}: {len(requesting)} master(s) waiting, none granted")
            winner = granted[0] if granted else None
            self.grants.append(winner.name if winner else None)

            await RisingEdge(self.dut.clk)           # the granted access happens here
            read = None
            if winner is not None:
                op = winner.current
                word = (op.addr >> 2) & (self.words - 1)
                if op.write:
                    if not op.addr & MMIO_BIT:       # MMIO stores never reach the SRAM
                        self.ref.write_word(word, op.data, op.strb)
                else:
                    assert not op.addr & MMIO_BIT, "test bug: the harness has no MMIO"
                    read = (winner, op, self.ref.read_word(word))
                winner.current = None
                winner.max_wait = max(winner.max_wait, winner.waited)
                winner.ready_at = self.cycle + 1 + random.randint(*winner.gap)
                self.busy_cycles += 1
            for m in requesting:
                if m is not winner:
                    m.waited += 1

            await ReadOnly()
            prev = None
            if read is not None:
                m, op, want = read
                src = as_int(self.dut.data_src)
                assert src == m.src_id, (
                    f"cycle {self.cycle}: bus data tagged for source {src}, not {m.name}")
                got = as_int(self.dut.bus_rdata)
                if want is not None:
                    assert got == want, (f"cycle {self.cycle}: {m.name} read {op.addr:#06x}: "
                                         f"got {got:#010x}, want {want:#010x}")
                m.reads.append((op, got))
                prev = (m, got)
            self.cycle += 1
            assert self.cycle - start < max_cycles, "bus run did not finish"

    async def idle(self, n: int = 1) -> None:
        """n cycles with nobody requesting."""
        for _ in range(n):
            await FallingEdge(self.dut.clk)
            for m in self.masters:
                m.drive(self.cycle)
            await ReadOnly()
            assert not any(as_int(m.gnt) for m in self.masters), "grant with no request"
            await RisingEdge(self.dut.clk)
            self.cycle += 1

    async def access(self, master: Master, op: Op) -> int | None:
        """One access on its own; returns the data for a read."""
        master.queue.append(op)
        await self.run()
        return None if op.write else master.reads[-1][1]

    async def fill_memory(self) -> None:
        """Write every SRAM word through the D-port, like the bootloader does."""
        for w in range(self.words):
            self.d0.queue.append(Op(True, self.word_addr(w), random.getrandbits(32)))
        await self.run()

    async def check_every_word(self) -> None:
        """Read every word back through the I-cache port and compare."""
        for w in range(self.words):
            self.ic0.queue.append(Op(False, self.word_addr(w)))
        await self.run()


async def setup(dut) -> SingleCoreBus:
    bus = SingleCoreBus(dut)
    await bus.reset()
    await bus.fill_memory()            # every word defined; last grant was the D-port
    return bus


# ------------------------------------------------------------------- tests

@cocotb.test(timeout_time=5, timeout_unit="ms")
async def alternating_back_to_back(dut):
    """B1: both masters request every cycle. Grants alternate strictly and
    the bus completes one access per cycle."""
    bus = await setup(dut)
    n = 300
    for _ in range(n):
        bus.d0.queue.append(Op(write=random.random() < 0.5,
                               addr=bus.word_addr(random.randrange(bus.words)),
                               data=random.getrandbits(32),
                               strb=random.choice(CORE_STROBES)))
        bus.ic0.queue.append(Op(False, bus.word_addr(random.randrange(bus.words))))
    start = len(bus.grants)
    await bus.run()
    log = bus.grants[start:]
    assert len(log) == 2 * n and None not in log, "the bus was idle while both waited"
    assert all(a != b for a, b in zip(log, log[1:])), "grants did not alternate"
    assert bus.d0.max_wait <= 1 and bus.ic0.max_wait <= 1


@cocotb.test()
async def store_then_fetch_next_cycle(dut):
    """B2: the D-port stores a word and the I-cache fetches it the very next
    cycle; the fetch sees the new value."""
    bus = await setup(dut)
    a = bus.word_addr(10)
    await bus.access(bus.ic0, Op(False, bus.word_addr(11)))   # last grant ic0: D-port goes first
    bus.d0.queue.append(Op(True, a, 0x2222_2222))
    bus.ic0.queue.append(Op(False, a))
    start = len(bus.grants)
    await bus.run()
    assert bus.grants[start:] == [bus.d0.name, bus.ic0.name], bus.grants[start:]
    assert bus.ic0.reads[-1][1] == 0x2222_2222, "fetch missed the store from the cycle before"


@cocotb.test()
async def fetch_then_store_same_word(dut):
    """B3: the I-cache fetches a word and the D-port stores to it the next
    cycle. The fetch returns the old word; a later fetch the new one."""
    bus = await setup(dut)
    a = bus.word_addr(20)
    await bus.access(bus.d0, Op(False, bus.word_addr(21)))    # last grant d0: I-cache goes first
    old = bus.ref.read_word(20 % bus.words)
    bus.ic0.queue.append(Op(False, a))
    bus.d0.queue.append(Op(True, a, 0x3333_3333))
    start = len(bus.grants)
    await bus.run()
    assert bus.grants[start:] == [bus.ic0.name, bus.d0.name], bus.grants[start:]
    assert bus.ic0.reads[-1][1] == old, "fetch saw a store granted after it"
    assert await bus.access(bus.ic0, Op(False, a)) == 0x3333_3333


@cocotb.test()
async def only_the_winner_reaches_sram(dut):
    """B4: only the granted master's request reaches the SRAM. Junk on idle
    or waiting masters, idle cycles, and stores to MMIO addresses (bit 12 set,
    e.g. LOCK at 0x1008 whose low bits match SRAM word 2) change nothing."""
    bus = await setup(dut)
    bus.d0.junk_when_idle = True
    bus.ic0.junk_when_idle = True
    await bus.idle(50)
    for _ in range(100):                                      # D-port shows junk, not requesting
        bus.ic0.queue.append(Op(False, bus.word_addr(random.randrange(bus.words))))
    await bus.run()
    for w in (2, 3):
        await bus.access(bus.d0, Op(True, MMIO_BIT | (w * 4), 0xFFFF_FFFF))
    bus.d0.junk_when_idle = False
    bus.ic0.junk_when_idle = False
    await bus.check_every_word()


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def core_timed_program(dut):
    """B5: one multi-cycle core's traffic. The program is written in, then
    the core repeats FETCH (I-cache miss), EXEC, and a load (S_MEM, S_MEM_W)
    or store (S_MEM). The two masters never compete, so every access is
    granted at once."""
    bus = await setup(dut)
    ninstr = bus.words // 4
    data_base = bus.words // 2
    program = [random.getrandbits(32) for _ in range(ninstr)]
    for i, word in enumerate(program):
        await bus.access(bus.d0, Op(True, 4 * i, word))
    pc = 0
    for _ in range(400):
        instr = await bus.access(bus.ic0, Op(False, 4 * pc))  # FETCH
        assert instr == program[pc], f"fetched {instr:#010x} at pc {4 * pc:#x}"
        await bus.idle(1)                                     # EXEC
        kind = random.choice(("alu", "alu", "load", "store"))
        addr = bus.word_addr(data_base + random.randrange(bus.words - data_base))
        if kind == "store":
            await bus.access(bus.d0, Op(True, addr, random.getrandbits(32),
                                        random.choice(CORE_STROBES)))   # S_MEM
        elif kind == "load":
            await bus.access(bus.d0, Op(False, addr))         # S_MEM, checked against the reference
            await bus.idle(1)                                 # S_MEM_W
        pc = (pc + 1) % ninstr
    assert bus.d0.max_wait == 0 and bus.ic0.max_wait == 0, "a lone core had to wait"


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def random_contention(dut):
    """B6: both masters issue random traffic with random gaps. Every read is
    checked, and neither master ever waits more than one cycle."""
    bus = await setup(dut)
    bus.d0.gap = (0, 3)
    bus.ic0.gap = (0, 3)
    for _ in range(2000):
        bus.d0.queue.append(Op(write=random.random() < 0.5,
                               addr=bus.word_addr(random.randrange(bus.words)),
                               data=random.getrandbits(32),
                               strb=random.choice(CORE_STROBES)))
        bus.ic0.queue.append(Op(False, bus.word_addr(random.randrange(bus.words))))
    await bus.run()
    assert bus.d0.max_wait <= 1 and bus.ic0.max_wait <= 1
    dut._log.info("bus busy on %d of %d cycles", bus.busy_cycles, bus.cycle)


# ------------------------------------------------------------------ pytest

@pytest.mark.parametrize("raw", [10, 11])
def test_sram_mem_bus(raw):
    run_cocotb(toplevel="sram_bus_harness",
               sources=[ROOT / "rtl/bus/arbiter.sv", HERE / "sram_bus_harness.sv"],
               parameters={"RAW": raw}, test_module="test_sram_bus",
               tag=f"sram_bus_raw{raw}", extra_env={"SRAM_RAW": str(raw)})
