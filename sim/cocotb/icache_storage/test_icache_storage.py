"""cocotb tests for rtl/cache/icache_storage.sv.

The storage holds the valid, tag and data arrays for both ways of the
2-way, 8-set instruction cache. It has one shared index, one write port per way
(a write enable, a tag and a word), combinational reads, and a synchronous
reset. A write always sets the valid bit of the way it writes.

The tests mirror the checklist for this block:
  reset            reset clears every valid bit, in both ways and all 8 sets
  way_isolation    a write to way 0 leaves way 1 alone (and the reverse)
  set_isolation    a write to one index leaves the other 7 untouched
  both_ways        both ways written in the same cycle
  combinational    changing the index changes the outputs in the same cycle
  tag_width        tags are 6 bits (1 valid + 6 tag + 32 data = 39-bit line)
plus a few extras: no write without its enable, reset beats a write, and a
random sequence checked against a plain-Python model.

Run with `make test-cocotb BLOCK=icache_storage`. SEED=<n> sets the seed of the
random test (default 1); the test prints the one it used.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

CLK_NS = 10
SETS = 8
WAYS = 2
SPEC_TAG_BITS = 6   # 39-bit line = 1 valid + 6 tag + 32 data (2 KiB RAM)
WORD_MASK = 0xFFFF_FFFF


class Bench:
    """Drives the storage and mirrors it in a Python model.

    Inputs change on the falling edge and the storage registers them on the
    next rising edge, so after tick() returns (a falling edge again) the write
    is visible on the outputs. The model maps (way, index) to (valid, tag,
    data); a way that was never written, or was reset, reads as invalid.
    """

    def __init__(self, dut):
        self.dut = dut
        self.tag_bits = len(dut.sec0_tag)
        self.tag_mask = (1 << self.tag_bits) - 1
        self.model = {}
        self.idle_inputs()

    def idle_inputs(self, index=0):
        d = self.dut
        d.reset.value = 0
        d.index.value = index
        d.sec0_write_en.value = 0
        d.sec1_write_en.value = 0
        d.sec0_write_data.value = 0
        d.sec1_write_data.value = 0
        d.sec0_write_tag.value = 0
        d.sec1_write_tag.value = 0

    async def tick(self):
        """One clock cycle: the inputs set since the last falling edge are
        registered on the rising edge, then we are back at a falling edge."""
        await RisingEdge(self.dut.clk)
        await FallingEdge(self.dut.clk)

    async def reset(self, cycles=2):
        self.idle_inputs()
        self.dut.reset.value = 1
        for _ in range(cycles):
            await self.tick()
        self.dut.reset.value = 0
        self.model.clear()

    async def write(self, index, way0=None, way1=None):
        """Write (tag, data) to way 0 and/or way 1 at `index` in one cycle."""
        d = self.dut
        d.index.value = index
        for way, entry in ((0, way0), (1, way1)):
            if entry is None:
                continue
            tag, data = entry
            getattr(d, f"sec{way}_write_en").value = 1
            getattr(d, f"sec{way}_write_tag").value = tag
            getattr(d, f"sec{way}_write_data").value = data
            self.model[(way, index)] = (1, tag & self.tag_mask, data)
        await self.tick()
        self.idle_inputs(index)

    async def read_entry(self, way, index):
        """Select `index`, let the combinational outputs settle, and return
        (valid, tag, data) of one way."""
        d = self.dut
        d.index.value = index
        await Timer(1, unit="ns")
        valid = int(getattr(d, f"sec{way}_valid").value)
        tag = int(getattr(d, f"sec{way}_tag").value)
        data = int(getattr(d, f"sec{way}_data").value)
        return valid, tag, data

    def expected(self, way, index):
        """The model's view of one entry. The tag and data of an invalid entry
        are don't-cares, so only the valid bit is compared for those."""
        return self.model.get((way, index), (0, None, None))

    async def check_entry(self, way, index, msg=""):
        got = await self.read_entry(way, index)
        exp = self.expected(way, index)
        where = f"way {way} index {index}" + (f" ({msg})" if msg else "")
        assert got[0] == exp[0], f"{where}: valid {got[0]}, expected {exp[0]}"
        if exp[0]:
            assert got[1] == exp[1], f"{where}: tag {got[1]:#x}, expected {exp[1]:#x}"
            assert got[2] == exp[2], f"{where}: data {got[2]:#010x}, expected {exp[2]:#010x}"

    async def check_all(self, msg=""):
        for index in range(SETS):
            for way in range(WAYS):
                await self.check_entry(way, index, msg)


async def new_bench(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    tb = Bench(dut)
    await FallingEdge(dut.clk)
    await tb.reset()
    return tb


def distinct(rng, tag_mask):
    """A random (tag, data) with a non-zero tag and word, so a stuck-at-zero
    array or a swapped way shows up."""
    return rng.randint(1, tag_mask), rng.randint(1, WORD_MASK)


# Reset

@cocotb.test()
async def reset_clears_every_valid_bit(dut):
    """Fill all 16 entries, then reset: every valid bit, in both ways and all
    8 sets, reads 0."""
    tb = await new_bench(dut)
    rng = random.Random(1)
    for index in range(SETS):
        await tb.write(index, distinct(rng, tb.tag_mask), distinct(rng, tb.tag_mask))
    for index in range(SETS):
        for way in range(WAYS):
            assert (await tb.read_entry(way, index))[0] == 1, \
                f"way {way} index {index} not valid after its write"

    await tb.reset()
    for index in range(SETS):
        for way in range(WAYS):
            valid = (await tb.read_entry(way, index))[0]
            assert valid == 0, f"way {way} index {index}: valid bit survived reset"


@cocotb.test()
async def reset_with_write_enable_high_stays_empty(dut):
    """reset has priority over a write in the same cycle (the core holds i_req
    high during reset, and a stray fill must not leave a valid line)."""
    tb = await new_bench(dut)
    d = dut
    d.reset.value = 1
    for index in range(SETS):
        d.index.value = index
        d.sec0_write_en.value = 1
        d.sec1_write_en.value = 1
        d.sec0_write_tag.value = tb.tag_mask
        d.sec1_write_tag.value = tb.tag_mask
        d.sec0_write_data.value = WORD_MASK
        d.sec1_write_data.value = WORD_MASK
        await tb.tick()
    await tb.reset()
    await tb.check_all("after reset with write enables high")


# Way isolation

@cocotb.test()
async def write_way0_reads_back_and_way1_is_unchanged(dut):
    """A write to way 0 reads back the same data, tag and valid bit, and way 1
    at the same index keeps what it held (invalid, then a previous write)."""
    tb = await new_bench(dut)
    rng = random.Random(2)
    index = 5

    # Way 1 empty: a write to way 0 must not make way 1 valid.
    w0 = distinct(rng, tb.tag_mask)
    await tb.write(index, way0=w0)
    assert await tb.read_entry(0, index) == (1, w0[0], w0[1])
    assert (await tb.read_entry(1, index))[0] == 0, "way 1 became valid"

    # Way 1 holding a line: a second write to way 0 must not disturb it.
    w1 = distinct(rng, tb.tag_mask)
    await tb.write(index, way1=w1)
    w0b = distinct(rng, tb.tag_mask)
    await tb.write(index, way0=w0b)
    assert await tb.read_entry(0, index) == (1, w0b[0], w0b[1])
    assert await tb.read_entry(1, index) == (1, w1[0], w1[1]), "way 1 was disturbed"


@cocotb.test()
async def write_way1_reads_back_and_way0_is_unchanged(dut):
    """The mirror of the way 0 test."""
    tb = await new_bench(dut)
    rng = random.Random(3)
    index = 2

    w1 = distinct(rng, tb.tag_mask)
    await tb.write(index, way1=w1)
    assert await tb.read_entry(1, index) == (1, w1[0], w1[1])
    assert (await tb.read_entry(0, index))[0] == 0, "way 0 became valid"

    w0 = distinct(rng, tb.tag_mask)
    await tb.write(index, way0=w0)
    w1b = distinct(rng, tb.tag_mask)
    await tb.write(index, way1=w1b)
    assert await tb.read_entry(1, index) == (1, w1b[0], w1b[1])
    assert await tb.read_entry(0, index) == (1, w0[0], w0[1]), "way 0 was disturbed"


@cocotb.test()
async def both_ways_written_in_the_same_cycle(dut):
    """Both write enables in one cycle store both lines, at every index."""
    tb = await new_bench(dut)
    rng = random.Random(4)
    for index in range(SETS):
        await tb.write(index, distinct(rng, tb.tag_mask), distinct(rng, tb.tag_mask))
    await tb.check_all("both ways written together")


# Set isolation

@cocotb.test()
async def write_leaves_the_other_seven_sets_untouched(dut):
    """With all 16 entries holding distinct lines, a write to each index in
    turn changes only that index."""
    tb = await new_bench(dut)
    rng = random.Random(5)
    for index in range(SETS):
        await tb.write(index, distinct(rng, tb.tag_mask), distinct(rng, tb.tag_mask))
    await tb.check_all("before overwrites")

    for index in range(SETS):
        for way in range(WAYS):
            entry = distinct(rng, tb.tag_mask)
            await tb.write(index, **{f"way{way}": entry})
            await tb.check_all(f"after overwriting way {way} index {index}")


@cocotb.test()
async def write_to_one_index_of_an_empty_cache_validates_only_it(dut):
    """From reset, a single write makes exactly one entry valid."""
    tb = await new_bench(dut)
    rng = random.Random(6)
    for index in range(SETS):
        for way in range(WAYS):
            await tb.reset()
            await tb.write(index, **{f"way{way}": distinct(rng, tb.tag_mask)})
            await tb.check_all(f"single write to way {way} index {index}")


# Reads

@cocotb.test()
async def reads_are_combinational(dut):
    """Changing the index changes the outputs in the same cycle, with no clock
    edge in between: same-cycle hits depend on it."""
    tb = await new_bench(dut)
    rng = random.Random(7)
    for index in range(SETS):
        await tb.write(index, distinct(rng, tb.tag_mask), distinct(rng, tb.tag_mask))

    # Sweep the index within one cycle. The clock edge is 5 ns away; each step
    # waits 1 ns, so no edge can register anything between steps.
    for index in list(range(SETS)) + list(reversed(range(SETS))):
        for way in range(WAYS):
            got = await tb.read_entry(way, index)
            _, tag, data = tb.expected(way, index)
            assert got == (1, tag, data), \
                f"way {way} index {index}: outputs did not follow the index in the same cycle"


@cocotb.test()
async def write_data_does_not_show_until_the_clock_edge(dut):
    """The write is registered: with the enable high, the old contents stay on
    the outputs until the rising edge, and the new ones appear after it."""
    tb = await new_bench(dut)
    rng = random.Random(8)
    index = 3
    old = distinct(rng, tb.tag_mask)
    await tb.write(index, way0=old)

    new = distinct(rng, tb.tag_mask)
    while new == old:
        new = distinct(rng, tb.tag_mask)
    dut.index.value = index
    dut.sec0_write_en.value = 1
    dut.sec0_write_tag.value = new[0]
    dut.sec0_write_data.value = new[1]
    await Timer(1, unit="ns")
    assert await tb.read_entry(0, index) == (1, old[0], old[1]), \
        "write showed through before the clock edge"
    await tb.tick()
    tb.idle_inputs(index)
    tb.model[(0, index)] = (1, new[0], new[1])
    assert await tb.read_entry(0, index) == (1, new[0], new[1])


# No write without an enable

@cocotb.test()
async def no_write_without_enable(dut):
    """Tag and data inputs change every cycle, but with both enables low
    nothing is stored: an empty cache stays empty, a full one is unchanged."""
    tb = await new_bench(dut)
    rng = random.Random(9)

    for index in range(SETS):
        dut.index.value = index
        for way in range(WAYS):
            getattr(dut, f"sec{way}_write_tag").value = tb.tag_mask
            getattr(dut, f"sec{way}_write_data").value = WORD_MASK
        await tb.tick()
    tb.idle_inputs()
    await tb.check_all("enables low on an empty cache")

    for index in range(SETS):
        await tb.write(index, distinct(rng, tb.tag_mask), distinct(rng, tb.tag_mask))
    for index in range(SETS):
        dut.index.value = index
        for way in range(WAYS):
            getattr(dut, f"sec{way}_write_tag").value = rng.randint(0, tb.tag_mask)
            getattr(dut, f"sec{way}_write_data").value = rng.randint(0, WORD_MASK)
        await tb.tick()
    tb.idle_inputs()
    await tb.check_all("enables low on a full cache")


# Tag width

@cocotb.test()
async def tags_are_six_bits_wide(dut):
    """The spec's 39-bit line is 1 valid + 6 tag + 32 data, with 2 KiB of RAM
    (RAW=11, tag = i_addr[10:5]). The tag ports must be 6 bits wide, and the
    top tag bits must survive a write and read back."""
    for name in ("sec0_tag", "sec1_tag", "sec0_write_tag", "sec1_write_tag"):
        width = len(getattr(dut, name))
        assert width == SPEC_TAG_BITS, f"{name} is {width} bits wide, the spec says {SPEC_TAG_BITS}"

    tb = await new_bench(dut)
    full = (1 << SPEC_TAG_BITS) - 1
    for index in range(SETS):
        # Tags that differ only in the upper bits (4 and 5) must stay distinct.
        await tb.write(index, (full, 0xA5A5_A5A5), (0b110000 | index, 0x5A5A_5A5A))
    await tb.check_all("6-bit tags")


# Random sequence against the model

@cocotb.test()
async def random_writes_and_resets(dut):
    """Random one- and two-way writes, with an occasional reset, checked
    against the model after every cycle."""
    seed = int(os.environ.get("SEED", "1"))
    dut._log.info(f"SEED={seed} (replay with SEED={seed})")
    rng = random.Random(seed)
    tb = await new_bench(dut)
    for _ in range(600):
        r = rng.random()
        if r < 0.03:
            await tb.reset(cycles=rng.randint(1, 2))
        else:
            index = rng.randrange(SETS)
            way0 = (rng.randint(0, tb.tag_mask), rng.getrandbits(32)) if rng.random() < 0.6 else None
            way1 = (rng.randint(0, tb.tag_mask), rng.getrandbits(32)) if rng.random() < 0.6 else None
            await tb.write(index, way0, way1)
        await tb.check_all()


def test_icache_storage():
    """pytest entry point: build icache_storage with Verilator and run the
    cocotb tests above."""
    run("icache_storage", ["rtl/cache/icache_storage.sv"], "icache_storage",
        "test_icache_storage")
