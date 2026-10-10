"""Level 1 (unit) tests: sram_mem together with its four sram_macro lanes.

The unit is driven directly through sram_mem's ports and checked against a
reference memory. Every test runs at 256, 512 and 1024 words per lane, and
with both lane models: sram_macro's behavioural memory and the OCD macro
models. Running the macro models also proves the wrapper's active-low
polarities are right: a wrong CEN or GWEN fails every test.

    U1  word_write_read_edges         full words at the first, middle and last word
    U2  every_strobe_pattern          all 16 wstrb values; unstrobed bytes keep their value
    U3  enable_low_blocks_writes      en = 0 never writes, whatever wstrb and wdata say
    U4  read_latency_is_one_cycle     rdata changes at the edge that samples the read, not before
    U5  rdata_holds_when_not_reading  idle cycles and full-word writes leave rdata unchanged
    U6  back_to_back_ordering         write->read, read->write, write->write on one word
    U7  one_access_per_cycle          a different word read every cycle
    U8  address_lines                 walking-one addresses: stuck, shorted or dropped bits
    U9  data_lines                    walking one and zero over all 32 data bits
    U10 march_full_memory             March C- over every word
    U11 random_against_model          random reads, writes and idle cycles vs the reference
    U12 partial_write_rdata           during a partial write, unstrobed lanes read and
                                      strobed lanes hold (what the bus must ignore)

Run with pytest; see README.md.
"""

from __future__ import annotations

import random

import cocotb
import pytest
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from sram_model import MASK32, ByteMemory, as_int, env_int, run_cocotb, start_clock


class SramPort:
    """Drives sram_mem's port one cycle per call, and keeps a reference copy
    of what memory should hold."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.words = env_int("SRAM_WORDS", 512)
        self.aw = self.words.bit_length() - 1
        self.ref = ByteMemory(self.words * 4)

    async def start(self, idle_cycles: int = 4) -> None:
        """Drive every input low, start the clock, then make one dummy read.

        The macro needs CEN high before its first access. The OCD model only
        counts that once it has seen CEN RISE after time zero: in a 4-state
        simulator CEN goes X -> 1 at start-up, but in Verilator it simply
        starts at 1, and without the dummy access the model silently ignores
        every access. The dummy read makes CEN fall and rise again. It is
        harmless for the behavioural model and for real silicon, where the
        design holds en low through reset."""
        for name in ("en", "addr", "wdata", "wstrb"):
            getattr(self.dut, name).value = 0
        start_clock(self.dut.clk)
        await ClockCycles(self.dut.clk, idle_cycles)
        await self.cycle(en=1, addr=0)               # dummy read: CEN falls...
        for _ in range(idle_cycles):                 # ...and rises again
            await self.cycle()

    async def cycle(self, en: int = 0, addr: int = 0, wdata: int = 0, wstrb: int = 0) -> int | None:
        """One cycle. Drive on the falling edge, let the SRAM sample on the
        rising edge, then return rdata as it is right after that edge
        (None if it holds X or Z)."""
        await FallingEdge(self.dut.clk)
        self.dut.en.value = en
        self.dut.addr.value = addr
        self.dut.wdata.value = wdata & MASK32
        self.dut.wstrb.value = wstrb
        await RisingEdge(self.dut.clk)
        await ReadOnly()
        if en and wstrb:
            self.ref.write_word(addr, wdata, wstrb)
        value = self.dut.rdata.value
        return int(value) if value.is_resolvable else None

    async def write(self, addr: int, data: int, strb: int = 0b1111) -> None:
        await self.cycle(en=1, addr=addr, wdata=data, wstrb=strb)

    async def read(self, addr: int) -> int | None:
        return await self.cycle(en=1, addr=addr)

    async def idle(self, n: int = 1, junk: bool = False) -> None:
        """en low. With junk=True, addr/wdata/wstrb carry random values that
        must be ignored."""
        for _ in range(n):
            if junk:
                await self.cycle(en=0, addr=random.randrange(self.words),
                                 wdata=random.getrandbits(32), wstrb=random.getrandbits(4))
            else:
                await self.cycle()

    async def check_read(self, addr: int, what: str = "") -> int | None:
        got = await self.read(addr)
        want = self.ref.read_word(addr)
        assert want is not None, f"test bug: word {addr:#x} was never written"
        assert got == want, (f"{what} read word {addr:#x}: got "
                             f"{'X' if got is None else f'{got:#010x}'}, want {want:#010x}")
        return got


async def setup(dut) -> SramPort:
    port = SramPort(dut)
    await port.start()
    return port


def merge(old: int, new: int, strb: int) -> int:
    """old with the strobed bytes replaced by new's."""
    out = old
    for lane in range(4):
        if (strb >> lane) & 1:
            mask = 0xFF << (8 * lane)
            out = (out & ~mask) | (new & mask)
    return out & MASK32


# ------------------------------------------------------------------- tests

@cocotb.test()
async def word_write_read_edges(dut):
    """U1: full-word write then read at the first, a middle and the last word."""
    port = await setup(dut)
    for addr, data in [(0, 0x1122_3344), (port.words // 2, 0xDEAD_BEEF), (port.words - 1, 0xCAFE_F00D)]:
        await port.write(addr, data)
        await port.check_read(addr, "U1")


@cocotb.test()
async def every_strobe_pattern(dut):
    """U2: each of the 16 wstrb values writes exactly the strobed bytes."""
    port = await setup(dut)
    for strb in range(16):
        addr = (strb * 7) % port.words
        await port.write(addr, 0xA5A5_A5A5)                     # known background
        await port.write(addr, 0x0123_4567, strb)
        got = await port.read(addr)
        want = merge(0xA5A5_A5A5, 0x0123_4567, strb)
        assert got == want, f"U2 wstrb={strb:04b}: got {got}, want {want:#010x}"


@cocotb.test()
async def enable_low_blocks_writes(dut):
    """U3: with en = 0 nothing is written, even with wstrb and wdata active."""
    port = await setup(dut)
    addr = 3 % port.words
    await port.write(addr, 0x1357_9BDF)
    for strb in (0b0001, 0b1100, 0b1111):
        await port.cycle(en=0, addr=addr, wdata=0xFFFF_FFFF, wstrb=strb)
    await port.check_read(addr, "U3 after en=0 cycles")


@cocotb.test()
async def read_latency_is_one_cycle(dut):
    """U4: rdata becomes the new word at the rising edge that samples the
    read, and keeps the previous value until then."""
    port = await setup(dut)
    a0, a1 = 1 % port.words, 2 % port.words
    await port.write(a0, 0x1111_1111)
    await port.write(a1, 0x2222_2222)
    assert await port.read(a0) == 0x1111_1111

    # drive the read of a1 and look at rdata BEFORE the sampling edge
    await FallingEdge(dut.clk)
    dut.en.value, dut.addr.value, dut.wstrb.value = 1, a1, 0
    await ReadOnly()
    assert as_int(dut.rdata) == 0x1111_1111, "U4: rdata changed before the clock edge"
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert as_int(dut.rdata) == 0x2222_2222, "U4: rdata not updated at the sampling edge"


@cocotb.test()
async def rdata_holds_when_not_reading(dut):
    """U5: idle cycles and full-word writes leave rdata as the last read left
    it. (Partial writes are U12.)"""
    port = await setup(dut)
    a0, a1 = 4 % port.words, 5 % port.words
    await port.write(a0, 0x0BAD_CAFE)
    assert await port.read(a0) == 0x0BAD_CAFE
    await port.idle(3, junk=True)
    assert as_int(dut.rdata) == 0x0BAD_CAFE, "U5: rdata changed during idle cycles"
    await port.write(a1, 0x7777_7777)
    assert as_int(dut.rdata) == 0x0BAD_CAFE, "U5: a full-word write changed rdata"


@cocotb.test()
async def back_to_back_ordering(dut):
    """U6: accesses to one word in consecutive cycles take effect in order."""
    port = await setup(dut)
    a = 6 % port.words
    await port.write(a, 0x1111_0001)
    assert await port.read(a) == 0x1111_0001, "U6: read straight after a write must see it"

    old = await port.read(a)                     # read, then a write the next cycle
    await port.write(a, 0x2222_0002)
    assert old == 0x1111_0001, "U6: a read must not see the following cycle's write"
    await port.check_read(a, "U6 read after read->write")

    await port.write(a, 0x3333_0003)             # two writes in a row: the last wins
    await port.write(a, 0x4444_0004)
    await port.check_read(a, "U6 write->write")


@cocotb.test()
async def one_access_per_cycle(dut):
    """U7: reading a different word every cycle returns each word in turn."""
    port = await setup(dut)
    addrs = [random.randrange(port.words) for _ in range(64)]
    for a in set(addrs):
        await port.write(a, random.getrandbits(32))
    for a in addrs:
        await port.check_read(a, "U7")


@cocotb.test()
async def address_lines(dut):
    """U8: words at address 0 and every power of two keep separate values.
    Catches a stuck, shorted or dropped address bit, such as a 10-bit address
    driving a 9-bit macro."""
    port = await setup(dut)
    addrs = [0] + [1 << k for k in range(port.aw)]
    for i, a in enumerate(addrs):
        await port.write(a, 0x5A00_0000 | (i << 8) | i)
    for a in addrs:
        await port.check_read(a, "U8")


@cocotb.test()
async def data_lines(dut):
    """U9: a walking one and a walking zero across all 32 data bits.
    Catches stuck bits and swapped byte lanes."""
    port = await setup(dut)
    a = (port.words // 3) or 1
    for bit in range(32):
        for data in (1 << bit, ~(1 << bit) & MASK32):
            await port.write(a, data)
            got = await port.read(a)
            assert got == data, f"U9 bit {bit}: got {got}, want {data:#010x}"


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def march_full_memory(dut):
    """U10: March C- over every word, with all-zero and all-one words.
    Visits every cell in both directions; catches coupling between words."""
    port = await setup(dut)
    zero, ones = 0x0000_0000, 0xFFFF_FFFF
    up, down = range(port.words), range(port.words - 1, -1, -1)

    for a in up:
        await port.write(a, zero)
    for order, expect, write in ((up, zero, ones), (up, ones, zero),
                                 (down, zero, ones), (down, ones, zero)):
        for a in order:
            got = await port.read(a)
            assert got == expect, f"U10 word {a:#x}: got {got}, want {expect:#010x}"
            await port.write(a, write)
    for a in up:
        await port.check_read(a, "U10 final")


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def random_against_model(dut):
    """U11: thousands of random reads, strobed writes and idle cycles with
    junk inputs, checked against the reference. Reproduce with SEED=<n>."""
    port = await setup(dut)
    for a in range(port.words):                  # every word defined first
        await port.write(a, random.getrandbits(32))
    for _ in range(5000):
        roll = random.random()
        a = random.choice([0, port.words - 1]) if random.random() < 0.05 else random.randrange(port.words)
        if roll < 0.2:
            await port.idle(junk=True)
        elif roll < 0.6:
            await port.write(a, random.getrandbits(32), random.randrange(1, 16))
        else:
            await port.check_read(a, "U11")


@cocotb.test()
async def partial_write_rdata(dut):
    """U12: during a partial write each lane does one of two things. A lane
    being written keeps its previous rdata byte; a lane not being written
    does a read, so its rdata byte becomes that word's current byte. The bus
    ignores rdata after a write, so this only documents the behaviour, and
    the behavioural model and the macro must agree on it."""
    port = await setup(dut)
    a, b = 7 % port.words, 8 % port.words
    await port.write(a, 0xAAAA_AAAA)
    await port.write(b, 0x4433_2211)
    assert await port.read(a) == 0xAAAA_AAAA
    for strb in (0b0001, 0b0110, 0b1000, 0b0101, 0b1110):
        rdata_before = as_int(dut.rdata)
        word_before = port.ref.read_word(b)
        got = await port.cycle(en=1, addr=b, wdata=random.getrandbits(32), wstrb=strb)
        want = merge(word_before, rdata_before, strb)
        assert got == want, (f"U12 wstrb={strb:04b}: rdata {got}, want {want:#010x} "
                             f"(strobed lanes hold {rdata_before:#010x}, "
                             f"others read {word_before:#010x})")


# ------------------------------------------------------------------ pytest

@pytest.mark.parametrize("model", ["behavioural", "macro"])
@pytest.mark.parametrize("words", [256, 512, 1024])
def test_sram_mem_unit(words, model):
    run_cocotb(words=words, macro=(model == "macro"), test_module="test_sram_unit")
