"""Two real core/cache pairs with independent Python memory responses.
Place beside dual_core_icache_harness.sv.
Tests:
    C1 Both pairs execute the same program.
    C2 Different programs at the same addresses remain independent.
    C3 Delaying one pair's memory does not stall the other.
    C4 Conflicts in one cache do not affect the other cache.
No shared bus or SRAM is included.
The programs use full-word stores and no data loads.
Cache prediction follows the supplied six-bit tags and fill-only FIFO policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.triggers import Timer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

sys.path.insert(0, str(HERE.parent))

from ref_model import run_cocotb

# ------------------------------------------------------------------
# Instruction encoders
# ------------------------------------------------------------------

def addi(rd, rs1, imm):
    assert -2048 <= imm < 2048

    return (
        ((imm & 0xFFF) << 20)
        | (rs1 << 15)
        | (rd << 7)
        | 0x13
    )


def sw(rs2, address):
    """Encode sw rs2, address(x0)."""
    assert 0 <= address < 2048
    assert address % 4 == 0

    return (
        ((address >> 5) << 25)
        | (rs2 << 20)
        | (2 << 12)
        | ((address & 31) << 7)
        | 0x23
    )


def jal(offset):
    """Encode jal x0, offset."""
    assert offset % 2 == 0
    assert -(1 << 20) <= offset < (1 << 20)

    imm = offset & 0x1FFFFF

    return (
        (((imm >> 20) & 1) << 31)
        | (((imm >> 1) & 0x3FF) << 21)
        | (((imm >> 11) & 1) << 20)
        | (((imm >> 12) & 0xFF) << 12)
        | 0x6F
    )


def loop_program(increment, result_address):
    """Add increment four times, then store the result."""
    program = {
        0x00: addi(1, 0, 4),
        0x04: addi(2, 0, 0),
        0x08: addi(2, 2, increment),
        0x0C: addi(1, 1, -1),
        0x10: 0xFE009CE3,       # bne x1, x0, -8
        0x14: sw(2, result_address),
        0x18: jal(0),
    }

    return {
        "program": program,
        "pcs": [0, 4] + [8, 12, 16] * 4 + [20],
        "stores": [(result_address, 4 * increment, 0b1111)],
    }


# ------------------------------------------------------------------
# One core-cache pair
# ------------------------------------------------------------------

class Pair:
    """Memory model and checks for one pair.

    Bench controls the clock for both pairs.
    """

    def __init__(self, dut, number, spec, delay):
        self.dut = dut
        self.n = number

        self.program = spec["program"]
        self.pcs = spec["pcs"]
        self.expected_stores = spec.get("stores", [])
        self.delay = delay

        self.retired = []
        self.fetches = []
        self.reads = []
        self.stores = []

        # Independent reference cache for this pair.
        self.lines = [[None, None] for _ in range(8)]
        self.victim = [0] * 8

        self.active = None
        self.wait_address = None
        self.wait_cycles = 0

        self.finish_cycle = None
        self.grant = False
        self.response = 0

    def sig(self, name):
        """Map a generic signal name to pair 0 or pair 1."""
        if name == "bus_rdata":
            return getattr(self.dut, f"bus{self.n}_rdata")

        if name in ("retire", "retire_pc"):
            return getattr(self.dut, f"{name}{self.n}")

        prefix, suffix = name.split("_", 1)
        return getattr(self.dut, f"{prefix}{self.n}_{suffix}")

    def get(self, name):
        return int(self.sig(name).value)

    @property
    def done(self):
        return self.finish_cycle is not None

    def drive(self, cycle):
        """Drive memory grants while the clock is low."""
        self.grant = False

        if not self.done:
            assert self.get("ic_wdata") == 0
            assert self.get("ic_wstrb") == 0

            if self.get("ic_req"):
                address = self.get("ic_addr")

                if self.wait_address is None:
                    self.wait_address = address
                    self.wait_cycles = 0

                assert address == self.wait_address, (
                    f"pair {self.n}: bus address changed before grant"
                )
                assert address % 4 == 0

                if self.wait_cycles >= self.delay:
                    assert address in self.program, (
                        f"pair {self.n}: unknown memory address "
                        f"{address:#x}"
                    )

                    self.grant = True
                    self.response = self.program[address]
                    self.reads.append(address)
                else:
                    self.wait_cycles += 1

            else:
                assert self.wait_address is None, (
                    f"pair {self.n}: request dropped before grant"
                )

        self.sig("ic_gnt").value = int(self.grant)

        # These programs only issue stores; accept immediately.
        self.sig("d_gnt").value = int(
            not self.done and self.get("d_req")
        )

    def check(self, cycle):
        """Check signals immediately before the accepting clock edge."""
        if self.done:
            return

        where = f"pair {self.n}, cycle {cycle}"

        # An outstanding fetch must remain stable.
        if self.active is not None:
            assert self.get("i_req"), (
                f"{where}: unfinished fetch dropped"
            )
            assert self.get("i_addr") == self.active["pc"], (
                f"{where}: stalled PC changed"
            )

        if self.get("i_req"):
            pc = self.get("i_addr")

            assert pc in self.program, (
                f"{where}: unknown PC {pc:#x}"
            )

            if self.active is None:
                index = (pc >> 2) & 7
                tag = (pc >> 5) & 63

                self.active = {
                    "pc": pc,
                    "index": index,
                    "tag": tag,
                    "hit": tag in self.lines[index],
                    "grant": None,
                }

            access = self.active

            if self.grant:
                assert not access["hit"], (
                    f"{where}: cache hit went to memory"
                )
                assert access["grant"] is None, (
                    f"{where}: duplicate grant"
                )
                assert self.get("ic_addr") == pc

                access["grant"] = cycle

            # Hits respond immediately.
            # Misses respond one cycle after the grant.
            due = access["hit"] or (
                access["grant"] is not None
                and cycle == access["grant"] + 1
            )

            assert bool(self.get("i_valid")) == due, (
                f"{where}: incorrect instruction response timing"
            )

            if due:
                assert self.get("i_rdata") == self.program[pc], (
                    f"{where}: wrong instruction at {pc:#x}"
                )

                if not access["hit"]:
                    index = access["index"]
                    way = self.victim[index]

                    self.lines[index][way] = access["tag"]
                    self.victim[index] = 1 - way

                self.fetches.append((pc, access["hit"]))
                self.active = None

        else:
            assert not self.get("i_valid"), (
                f"{where}: unsolicited instruction response"
            )
            assert not self.get("ic_req"), (
                f"{where}: memory request without a fetch"
            )

        # Check program-result stores.
        if self.get("d_req"):
            store = (
                self.get("d_addr"),
                self.get("d_wdata"),
                self.get("d_wstrb"),
            )

            position = len(self.stores)

            assert position < len(self.expected_stores), (
                f"{where}: unexpected data request {store}"
            )
            assert store == self.expected_stores[position], (
                f"{where}: incorrect store {store}"
            )

            self.stores.append(store)

        # Check completed instruction addresses.
        if self.get("retire"):
            position = len(self.retired)
            pc = self.get("retire_pc")

            assert position < len(self.pcs), (
                f"{where}: extra retirement at {pc:#x}"
            )
            assert pc == self.pcs[position], (
                f"{where}: retired {pc:#x}, "
                f"expected {self.pcs[position]:#x}"
            )

            self.retired.append(pc)

            if len(self.retired) == len(self.pcs):
                assert self.stores == self.expected_stores
                assert [
                    address for address, _ in self.fetches
                ] == self.pcs

                self.finish_cycle = cycle

    def respond(self):
        """Supply read data after the grant's accepting edge."""
        if self.grant:
            self.sig("bus_rdata").value = self.response
            self.wait_address = None


# ------------------------------------------------------------------
# Both pairs and their shared clock
# ------------------------------------------------------------------

class Bench:
    def __init__(self, dut, specs, delays=(0, 0)):
        self.dut = dut
        self.pairs = [
            Pair(dut, number, specs[number], delays[number])
            for number in range(2)
        ]

    async def run(self, limit=3000):
        dut = self.dut

        dut.clk.value = 0
        dut.rst.value = 1
        dut.flush.value = 1

        for pair in self.pairs:
            for name in (
                "ic_gnt",
                "bus_rdata",
                "d_gnt",
                "d_rdata",
            ):
                pair.sig(name).value = 0

        # Synchronous reset for three rising edges.
        for _ in range(3):
            await Timer(5, "ns")
            dut.clk.value = 1

            await Timer(5, "ns")
            dut.clk.value = 0

        dut.rst.value = 0
        dut.flush.value = 0

        for cycle in range(limit):
            # Let the hardware outputs settle with the clock low.
            await Timer(2, "ns")

            for pair in self.pairs:
                pair.drive(cycle)

            await Timer(3, "ns")

            for pair in self.pairs:
                pair.check(cycle)

            # Both pairs accept their transactions on the same edge.
            dut.clk.value = 1

            await Timer(1, "ns")

            for pair in self.pairs:
                pair.respond()

            await Timer(4, "ns")
            dut.clk.value = 0

            if all(pair.done for pair in self.pairs):
                for pair in self.pairs:
                    dut._log.info(
                        "pair %d: retired=%d reads=%d "
                        "hits=%d finished=%d",
                        pair.n,
                        len(pair.retired),
                        len(pair.reads),
                        sum(hit for _, hit in pair.fetches),
                        pair.finish_cycle,
                    )
                return

        raise AssertionError(
            "Timeout: retired counts "
            f"{[len(pair.retired) for pair in self.pairs]}"
        )


# ------------------------------------------------------------------
# Cocotb tests
# ------------------------------------------------------------------

@cocotb.test()
async def both_run_same_program(dut):
    """C1: both cores execute the same program independently."""
    spec = loop_program(3, 0x100)
    bench = Bench(dut, [spec, spec])

    await bench.run()

    for pair in bench.pairs:
        assert pair.reads == [0, 4, 8, 12, 16, 20]
        assert sum(hit for _, hit in pair.fetches) == 9


@cocotb.test()
async def different_programs_same_addresses(dut):
    """C2: identical PCs may contain different instructions per pair."""
    bench = Bench(
        dut,
        [
            loop_program(3, 0x100),
            loop_program(7, 0x200),
        ],
    )

    await bench.run()

    assert bench.pairs[0].stores == [(0x100, 12, 15)]
    assert bench.pairs[1].stores == [(0x200, 28, 15)]


@cocotb.test()
async def stalling_one_pair_does_not_stall_other(dut):
    """C3: delaying pair 0's memory does not delay pair 1's execution."""
    bench = Bench(
        dut,
        [
            loop_program(3, 0x100),
            loop_program(7, 0x200),
        ],
        delays=(10, 0),
    )

    await bench.run()

    assert (
        bench.pairs[1].finish_cycle
        < bench.pairs[0].finish_cycle
    )


@cocotb.test()
async def conflicts_are_local(dut):
    """C4: pair 0 thrashes one set while pair 1 retains its loop."""
    conflict = {
        "program": {
            0x00: jal(32),
            0x20: jal(32),
            0x40: jal(-64),
        },
        "pcs": [0x00, 0x20, 0x40] * 12,
    }

    bench = Bench(
        dut,
        [
            conflict,
            loop_program(3, 0x100),
        ],
    )

    await bench.run()

    assert bench.pairs[0].reads == conflict["pcs"]
    assert not any(
        hit for _, hit in bench.pairs[0].fetches
    )

    assert bench.pairs[1].reads == [0, 4, 8, 12, 16, 20]
    assert sum(
        hit for _, hit in bench.pairs[1].fetches
    ) == 9


# ------------------------------------------------------------------
# Pytest entry point: compile hardware and launch cocotb
# ------------------------------------------------------------------

def test_dual_core_icache():
    harness = HERE / "dual_core_icache_harness.sv"

    sources = [
        *sorted((ROOT / "rtl/core").glob("*.sv")),
        *sorted((ROOT / "rtl/cache").glob("*.sv")),
        harness,
    ]

    assert harness.is_file(), f"Missing harness: {harness}"

    run_cocotb(
        toplevel="dual_core_icache_harness",
        sources=sources,
        parameters={},
        test_module="test_dual_core_icache",
        tag="dual_core_icache",
    )