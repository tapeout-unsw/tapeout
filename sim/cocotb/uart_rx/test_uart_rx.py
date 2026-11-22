"""cocotb unit tests for rtl/periph/uart_rx.sv.

Two builds: a fast one with 16 clock cycles per bit, and the real 100 MHz /
115200 baud one (868 cycles per bit) for timing-tolerance tests.

Python drives the rx pin one clock at a time (changing on the falling edge) and
a monitor collects every rx_valid pulse, checking that it lasts one cycle.

  every_byte             all 256 byte values, clean frames with idle between
  back_to_back           frames with no idle between stop and next start
  false_start_rejected   UART-1: rx pulled low for less than half a bit, then
                         high: no byte, the receiver returns to IDLE, and a
                         real frame straight afterwards is received correctly
                         (coverage report A4)
  glitch_lengths         the boundary: glitches up to the longest rejected
                         length are ignored; a frame still follows
  baud_tolerance         (real build) the sender's bit time off by -3%..+3%
  reset_mid_frame        rst during a frame: no byte, next frame received
  stop_bit_low           a frame whose stop bit is 0 (framing error): documents
                         that the receiver does not check the stop bit

Run with `make test-cocotb BLOCK=uart_rx`. SEED=<n> sets the seed.
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

CPB = int(os.environ.get("UART_CPB", "16"))       # cycles per bit of this build
HALF = CPB // 2
FAST = CPB < 100


class Bench:
    def __init__(self, dut):
        self.d = dut
        self.got = []
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        d = self.d
        prev = 0
        while True:
            await RisingEdge(d.clk)
            await Timer(1, "ns")
            v = int(d.rx_valid.value)
            if v:
                assert not prev, "rx_valid high for more than one cycle"
                self.got.append(int(d.rx_data.value))
            prev = v

    async def level(self, v, cycles):
        for _ in range(int(cycles)):
            await FallingEdge(self.d.clk)
            self.d.rx.value = v

    async def frame(self, byte, bit=CPB, stop=1, idle=0):
        """Send one frame with a (possibly fractional) bit time in cycles."""
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [stop]
        t = 0.0
        for b in bits:
            t0 = round(t); t += bit
            await self.level(b, round(t) - t0)
        if stop == 0:
            await self.level(1, CPB)
        await self.level(1, idle)

    async def expect(self, data):
        await self.level(1, 2 * CPB)
        assert self.got == list(data), f"received {self.got}, expected {list(data)}"
        self.got.clear()


async def new_bench(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rx.value = 1
    dut.rst.value = 1
    for _ in range(4):
        await FallingEdge(dut.clk)
    dut.rst.value = 0
    tb = Bench(dut)
    await tb.level(1, 4)
    return tb


@cocotb.test(skip=not FAST)
async def every_byte(dut):
    tb = await new_bench(dut)
    for b in range(256):
        await tb.frame(b, idle=3)
    await tb.expect(range(256))


@cocotb.test()
async def back_to_back(dut):
    tb = await new_bench(dut)
    rng = random.Random(int(os.environ.get("SEED", "1")))
    data = [rng.getrandbits(8) for _ in range(64 if FAST else 4)]
    for b in data:
        await tb.frame(b)
    await tb.expect(data)


@cocotb.test()
async def false_start_rejected(dut):
    """A4 / UART-1."""
    tb = await new_bench(dut)
    for glitch in (1, 2, HALF // 2):
        await tb.level(0, glitch)
        await tb.level(1, HALF + 4)           # past the mid-start sample point
        assert int(dut.state.value) == 0, f"not back in IDLE after a {glitch}-cycle glitch"
        await tb.expect([])
        await tb.frame(0xA5)                   # a real frame straight afterwards
        await tb.expect([0xA5])


@cocotb.test(skip=not FAST)
async def glitch_lengths(dut):
    """Every glitch length below half a bit (less the 2-cycle synchroniser
    slack) is rejected; each is followed immediately by a good frame."""
    tb = await new_bench(dut)
    for glitch in range(1, HALF - 1):
        await tb.level(0, glitch)
        await tb.level(1, HALF + 4)
        await tb.frame(glitch)
    await tb.expect(list(range(1, HALF - 1)))


@cocotb.test(skip=FAST)
async def baud_tolerance(dut):
    """Sender clock off by up to +-3%: mid-bit sampling still lands in each bit."""
    tb = await new_bench(dut)
    data = []
    for err in (-0.03, -0.015, 0.0, 0.015, 0.03):
        for b in (0x55, 0x00, 0xFF, 0x81):
            await tb.frame(b, bit=CPB * (1 + err), idle=CPB)
            data.append(b)
    await tb.expect(data)


@cocotb.test()
async def reset_mid_frame(dut):
    tb = await new_bench(dut)
    await tb.level(0, CPB)                     # start bit
    await tb.level(1, 3 * CPB)                 # some data bits
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    await tb.level(1, 2)
    dut.rst.value = 0
    await tb.level(1, 12 * CPB)                # rest of the frame time, line idle
    await tb.expect([])
    await tb.frame(0x3C)
    await tb.expect([0x3C])


@cocotb.test(skip=not FAST)
async def stop_bit_low(dut):
    """Framing error: the byte is still delivered (no stop-bit check)."""
    tb = await new_bench(dut)
    await tb.frame(0x42, stop=0)
    await tb.expect([0x42])
    await tb.frame(0x24)
    await tb.expect([0x24])


def test_uart_rx():
    """pytest entry point: a fast build and the real 100 MHz / 115200 one."""
    for name, clk, baud in (("fast", 1_600_000, 100_000), ("real", 100_000_000, 115_200)):
        os.environ["UART_CPB"] = str(clk // baud)
        run("uart_rx", ["rtl/periph/uart_rx.sv"], "uart_rx", "test_uart_rx",
            parameters={"CLK_FREQ": clk, "BAUD_RATE": baud}, tag=f"_{name}")
