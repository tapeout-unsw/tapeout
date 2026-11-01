"""cocotb unit tests for rtl/cache/dcache_snoop.sv.

dcache_snoop watches the bus writes the arbiter grants and tells this core's
data cache which line to drop (inv0, inv1), and whether the line being filled
is already stale (kill_fill). It is combinational, so the tests set the inputs,
wait 1 ns and read the outputs; there is no clock.

Parameters under test: RAW = 10 and 11, and OWN_SRC = 1 and 2 (core 0's and
core 1's data port). The pytest function builds all four.

Directed tests, one per rule in the header of dcache_snoop.sv:
  which bus writes count (a remote write) and which never do
  invalidate: way 0, way 1, both, wrong tag, invalid line, partial strobes
  tag aliasing: address bits above the RAM size are ignored (RAW = 10)
  kill_fill: only in the FILL cycle, same word, byte offset ignored
A random test compares every output with a model on 30000 vectors biased
towards matches.

Run with `make test-cocotb BLOCK=dcache_snoop`. SEED=<n> sets the seed of the
random test (default 1).
"""

import os
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

BOOT, D0, D1, IC0, IC1 = 0, 1, 2, 3, 4   # b_src


def addr(index=0, tag=0, offset=0, mmio=0, bit11=0):
    """13-bit byte address: [12] MMIO, [11], [10:5] tag, [4:2] index, [1:0] offset."""
    return (mmio << 12) | (bit11 << 11) | (tag << 5) | (index << 2) | offset


class Snoop:
    """Drives dcache_snoop and mirrors it with a model written from the header."""

    def __init__(self, dut):
        self.dut = dut
        self.raw = int(dut.RAW.value)
        self.own = int(dut.OWN_SRC.value)
        self.tag_mask = (1 << (self.raw - 5)) - 1
        dut._log.info(f"RAW={self.raw} OWN_SRC={self.own}")

    def remote_write(self, i):
        return bool(i["b_valid"] and i["b_wstrb"] and not (i["b_addr"] >> 12) & 1
                    and i["b_src"] != self.own)

    def model(self, i):
        rw = self.remote_write(i)
        index = (i["b_addr"] >> 2) & 7
        tag = (i["b_addr"] >> 5) & 0x3F

        def same_tag(t):
            return ((t ^ tag) & self.tag_mask) == 0
        word_mask = ((1 << self.raw) - 1) & ~3
        return dict(
            snoop_index=index,
            inv0=int(rw and i["sec0_valid"] and same_tag(i["sec0_tag"])),
            inv1=int(rw and i["sec1_valid"] and same_tag(i["sec1_tag"])),
            kill_fill=int(rw and i["in_fill"]
                          and (i["b_addr"] & word_mask) == (i["miss_addr"] & word_mask)),
        )

    async def drive(self, check=True, **kw):
        """Apply the inputs (defaults: an idle bus, empty storage), wait, return
        the outputs, and compare them with the model."""
        i = dict(b_valid=0, b_addr=0, b_wstrb=0, b_src=D1 if self.own == D0 else D0,
                 sec0_valid=0, sec0_tag=0, sec1_valid=0, sec1_tag=0,
                 in_fill=0, miss_addr=0)
        i.update(kw)
        for n, v in i.items():
            getattr(self.dut, n).value = v
        await Timer(1, "ns")
        out = {n: int(getattr(self.dut, n).value)
               for n in ("snoop_index", "inv0", "inv1", "kill_fill")}
        if check:
            want = self.model(i)
            assert out == want, f"inputs {i}:\n  got  {out}\n  want {want}"
        return out

    @property
    def other(self):
        """A b_src that is not ours, among the data ports."""
        return D1 if self.own == D0 else D0


async def new_snoop(dut):
    return Snoop(dut)


def line(valid, tag):
    return dict(valid=valid, tag=tag)


# Which bus writes count

@cocotb.test()
async def remote_write_invalidates_a_matching_line(dut):
    """A write by another requester drops the line with the same index and tag,
    in whichever way it is, and only that way."""
    s = await new_snoop(dut)
    a = addr(index=5, tag=3)
    w = dict(b_valid=1, b_addr=a, b_wstrb=0xF, b_src=s.other)
    out = await s.drive(**w, sec0_valid=1, sec0_tag=3, sec1_valid=1, sec1_tag=9)
    assert (out["snoop_index"], out["inv0"], out["inv1"]) == (5, 1, 0)
    out = await s.drive(**w, sec0_valid=1, sec0_tag=9, sec1_valid=1, sec1_tag=3)
    assert (out["inv0"], out["inv1"]) == (0, 1)
    out = await s.drive(**w, sec0_valid=1, sec0_tag=3, sec1_valid=1, sec1_tag=3)
    assert (out["inv0"], out["inv1"]) == (1, 1), "both ways, if both somehow match"
    out = await s.drive(**w, sec0_valid=1, sec0_tag=4, sec1_valid=1, sec1_tag=2)
    assert (out["inv0"], out["inv1"]) == (0, 0), "a different tag is a different word"
    out = await s.drive(**w, sec0_valid=0, sec0_tag=3, sec1_valid=0, sec1_tag=3)
    assert (out["inv0"], out["inv1"]) == (0, 0), "an invalid line has nothing to drop"


@cocotb.test()
async def snoop_index_follows_the_bus_address(dut):
    """The storage is read at the bus write's set, whatever the write is."""
    s = await new_snoop(dut)
    for index in range(8):
        out = await s.drive(b_addr=addr(index=index, tag=7))
        assert out["snoop_index"] == index


@cocotb.test()
async def every_remote_requester_counts(dut):
    """The bootloader (src 0), the other data port and the I-caches all
    invalidate; the cache's own port never does."""
    s = await new_snoop(dut)
    a = addr(index=2, tag=6)
    for src in (BOOT, D0, D1, IC0, IC1):
        out = await s.drive(b_valid=1, b_addr=a, b_wstrb=0xF, b_src=src,
                            sec0_valid=1, sec0_tag=6, sec1_valid=1, sec1_tag=6,
                            in_fill=1, miss_addr=a)
        want = 0 if src == s.own else 1
        assert (out["inv0"], out["inv1"], out["kill_fill"]) == (want, want, want), f"src {src}"


@cocotb.test()
async def things_that_are_not_remote_writes_do_nothing(dut):
    """A read (wstrb 0000), an idle bus (b_valid low), an MMIO address and
    nobody (b_src = 5) must never invalidate or kill a fill."""
    s = await new_snoop(dut)
    a = addr(index=1, tag=4)
    hot = dict(sec0_valid=1, sec0_tag=4, sec1_valid=1, sec1_tag=4, in_fill=1, miss_addr=a)
    cases = {
        "read": dict(b_valid=1, b_addr=a, b_wstrb=0, b_src=s.other),
        "idle bus": dict(b_valid=0, b_addr=a, b_wstrb=0xF, b_src=s.other),
        "nobody": dict(b_valid=0, b_addr=a, b_wstrb=0xF, b_src=5),
        "MMIO": dict(b_valid=1, b_addr=addr(index=1, tag=4, mmio=1), b_wstrb=0xF, b_src=s.other),
        "own write": dict(b_valid=1, b_addr=a, b_wstrb=0xF, b_src=s.own),
    }
    for name, c in cases.items():
        out = await s.drive(**c, **hot)
        assert (out["inv0"], out["inv1"], out["kill_fill"]) == (0, 0, 0), name


@cocotb.test()
async def partial_stores_drop_the_whole_line(dut):
    """sb and sh strobes (any non-zero wstrb) invalidate the line like a word
    store: the line is refetched on the next load."""
    s = await new_snoop(dut)
    a = addr(index=7, tag=1)
    for strb in range(1, 16):
        out = await s.drive(b_valid=1, b_addr=a, b_wstrb=strb, b_src=s.other,
                            sec0_valid=1, sec0_tag=1)
        assert out["inv0"] == 1, f"wstrb {strb:04b}"


@cocotb.test()
async def byte_offset_is_ignored(dut):
    """The two low address bits do not matter: it is the same word."""
    s = await new_snoop(dut)
    for off in range(4):
        out = await s.drive(b_valid=1, b_addr=addr(index=3, tag=2, offset=off),
                            b_wstrb=1, b_src=s.other, sec1_valid=1, sec1_tag=2,
                            in_fill=1, miss_addr=addr(index=3, tag=2))
        assert (out["inv1"], out["kill_fill"]) == (1, 1)


# Tag aliasing

@cocotb.test()
async def addresses_above_the_ram_size_alias(dut):
    """addr_decode uses b_addr[RAW-1:2], so with RAW = 10 an address with bit 10
    set is the same RAM word as the one with bit 10 clear. The snoop must drop
    the line then, whichever form the cache stored. With RAW = 11, bit 10 is a
    real address bit and the two words are different."""
    s = await new_snoop(dut)
    alias = 1 if s.raw == 10 else 0
    write = addr(index=4, tag=0b000011)                      # bit 10 clear
    cached = 0b100011                                        # the same with bit 10 set
    out = await s.drive(b_valid=1, b_addr=write, b_wstrb=0xF, b_src=s.other,
                        sec0_valid=1, sec0_tag=cached, in_fill=1,
                        miss_addr=addr(index=4, tag=cached))
    assert out["inv0"] == alias
    assert out["kill_fill"] == alias
    # Bit 9 is a real address bit for both sizes.
    out = await s.drive(b_valid=1, b_addr=write, b_wstrb=0xF, b_src=s.other,
                        sec0_valid=1, sec0_tag=0b000001, in_fill=1,
                        miss_addr=addr(index=4, tag=0b000001))
    assert (out["inv0"], out["kill_fill"]) == (0, 0)


# Fill race

@cocotb.test()
async def kill_fill_only_in_the_fill_cycle_for_the_same_word(dut):
    """A remote write to the word being filled kills the fill in the FILL cycle
    only; a write to another word, or in any other cycle, does not."""
    s = await new_snoop(dut)
    miss = addr(index=6, tag=5)
    w = dict(b_valid=1, b_wstrb=0xF, b_src=s.other, miss_addr=miss)
    out = await s.drive(**w, b_addr=miss, in_fill=1)
    assert out["kill_fill"] == 1
    out = await s.drive(**w, b_addr=miss, in_fill=0)
    assert out["kill_fill"] == 0, "outside the FILL cycle the SRAM read has not happened or is safe"
    for other in (addr(index=6, tag=4), addr(index=5, tag=5), addr(index=0, tag=0)):
        out = await s.drive(**w, b_addr=other, in_fill=1)
        assert out["kill_fill"] == 0, f"{other:#06x} is not the word being filled"
    # The kill does not depend on what the storage holds: the line is not in it yet.
    out = await s.drive(**w, b_addr=miss, in_fill=1, sec0_valid=0, sec1_valid=0)
    assert out["kill_fill"] == 1


@cocotb.test()
async def kill_fill_ignores_mmio_with_the_same_low_bits(dut):
    """An MMIO write whose low address bits equal the miss address is not a RAM
    write: it must not kill the fill or drop a line."""
    s = await new_snoop(dut)
    miss = addr(index=2, tag=2)
    out = await s.drive(b_valid=1, b_addr=miss | (1 << 12), b_wstrb=0xF, b_src=s.other,
                        in_fill=1, miss_addr=miss, sec0_valid=1, sec0_tag=2)
    assert (out["inv0"], out["kill_fill"]) == (0, 0)


# Random

@cocotb.test()
async def random_vectors_match_the_model(dut):
    """30000 random input vectors, with tags, indices and strobes drawn from
    small sets so that matches are common, compared with the model."""
    seed = int(os.environ.get("SEED", "1"))
    dut._log.info(f"SEED={seed} (replay with SEED={seed})")
    rng = random.Random(seed)
    s = await new_snoop(dut)
    tags = [0, 1, 3, 0b100001, 0b100011, 0b111111]
    seen = dict(inv0=0, inv1=0, kill_fill=0)
    for _ in range(30000):
        index = rng.randrange(8)
        b_addr = addr(index=index, tag=rng.choice(tags), offset=rng.randrange(4),
                      mmio=rng.random() < 0.1, bit11=rng.random() < 0.2)
        out = await s.drive(
            b_valid=rng.random() < 0.8,
            b_addr=b_addr,
            b_wstrb=rng.choice([0, 0, 1, 3, 0xC, 0xF, rng.randrange(16)]),
            b_src=rng.randrange(6),
            sec0_valid=rng.random() < 0.7, sec0_tag=rng.choice(tags),
            sec1_valid=rng.random() < 0.7, sec1_tag=rng.choice(tags),
            in_fill=rng.random() < 0.5,
            miss_addr=addr(index=rng.choice([index, rng.randrange(8)]),
                           tag=rng.choice(tags), offset=rng.randrange(4),
                           bit11=rng.random() < 0.2) | (int(rng.random() < 0.05) << 12))
        for k in seen:
            seen[k] += out[k]
    dut._log.info(f"outputs seen high: {seen}")
    assert all(seen.values()), f"the random vectors never raised {[k for k, v in seen.items() if not v]}"


@pytest.mark.parametrize("raw", [10, 11])
@pytest.mark.parametrize("own", [1, 2])
def test_dcache_snoop(raw, own):
    """pytest entry point: build dcache_snoop with Verilator for both RAM plans
    and both cores' data ports, and run the cocotb tests above."""
    run("dcache_snoop", ["rtl/cache/dcache_snoop.sv"], "dcache_snoop",
        "test_dcache_snoop", parameters={"RAW": raw, "OWN_SRC": own},
        tag=f"_raw{raw}_src{own}")
