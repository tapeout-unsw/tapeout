"""cocotb unit tests for rtl/periph/uart_tx.sv.

Two builds: 16 cycles per bit (fast) and the real 100 MHz / 115200 baud.
A Python line monitor samples tx every cycle and decodes frames by run
lengths: the start bit and each data bit must last exactly one bit time, the
stop bit at least one, and the line idles high.

  idle_high          after reset tx is 1 and tx_busy is 0
  every_byte         all 256 values, one at a time, waiting for tx_busy to fall
  busy_window        tx_busy rises the cycle after tx_start and lasts exactly
                     10 bit times (start, 8 data, stop)
  start_while_busy   tx_start (with other data) during a frame is ignored
  data_latched       tx_data changing after tx_start does not change the frame
  back_to_back       tx_start held high: frames follow with no gap but each
                     still has a full stop bit
  reset_mid_frame    rst mid-frame: tx high and tx_busy low at once
  loopback           (fast build) uart_tx's line decoded by the same rules as
                     uart_rx's mid-bit sampler

Run with `make test-cocotb BLOCK=uart_tx`. SEED=<n> sets the seed.
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

CPB = int(os.environ.get("UART_CPB", "16"))
FAST = CPB < 100


class Bench:
    def __init__(self, dut):
        self.d = dut
        self.frames = []           # decoded bytes
        self.errors = []
        self.line = []             # tx level per cycle (kept short)
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        """Wait for a falling edge on tx, then take 10 bit windows of CPB
        samples each: start, 8 data bits LSB first, stop. Every sample in a
        window must have the same level, start 0 and stop 1."""
        d = self.d
        prev = 1
        while True:
            await RisingEdge(d.clk)
            await Timer(1, "ns")
            lvl = int(d.tx.value)
            if not (prev == 1 and lvl == 0):
                prev = lvl
                continue
            samples = [lvl]
            while len(samples) < 10 * CPB:
                await RisingEdge(d.clk)
                await Timer(1, "ns")
                samples.append(int(d.tx.value))
            win = [samples[k * CPB:(k + 1) * CPB] for k in range(10)]
            bad = [k for k, w in enumerate(win) if len(set(w)) != 1]
            if bad:
                self.errors.append(f"bit windows {bad} not stable for {CPB} cycles")
            elif win[0][0] != 0 or win[9][0] != 1:
                self.errors.append("missing start or stop bit")
            else:
                self.frames.append(sum(win[1 + i][0] << i for i in range(8)))
            prev = samples[-1]

    async def send(self, byte, wait=True):
        d = self.d
        await FallingEdge(d.clk)
        d.tx_start.value, d.tx_data.value = 1, byte
        await FallingEdge(d.clk)
        d.tx_start.value = 0
        d.tx_data.value = (~byte) & 0xFF
        assert int(d.tx_busy.value) == 1, "tx_busy not high the cycle after tx_start"
        if wait:
            await self.idle()

    async def idle(self):
        d = self.d
        n = 0
        while int(d.tx_busy.value):
            await FallingEdge(d.clk)
            n += 1
            assert n < 12 * CPB, "tx_busy stuck high"
        return n

    async def expect(self, data):
        for _ in range(2 * CPB):
            await FallingEdge(self.d.clk)
        assert not self.errors, self.errors[:5]
        assert self.frames == list(data), f"decoded {self.frames}, expected {list(data)}"
        self.frames.clear()


async def new_bench(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.tx_start.value = 0
    dut.tx_data.value = 0
    dut.rst.value = 1
    for _ in range(3):
        await FallingEdge(dut.clk)
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    return Bench(dut)


@cocotb.test()
async def idle_high(dut):
    tb = await new_bench(dut)
    for _ in range(3 * CPB):
        await FallingEdge(dut.clk)
        assert int(dut.tx.value) == 1 and int(dut.tx_busy.value) == 0
    await tb.expect([])


@cocotb.test(skip=not FAST)
async def every_byte(dut):
    tb = await new_bench(dut)
    for b in range(256):
        await tb.send(b)
    await tb.expect(range(256))


@cocotb.test()
async def busy_window(dut):
    """tx_busy lasts exactly 10 bit times."""
    tb = await new_bench(dut)
    await tb.send(0x00, wait=False)
    n = await tb.idle()
    assert n == 10 * CPB, f"tx_busy high for {n} cycles, expected {10 * CPB}"
    await tb.expect([0x00])


@cocotb.test()
async def start_while_busy(dut):
    tb = await new_bench(dut)
    d = dut
    await tb.send(0x5A, wait=False)
    for k in range(5 * CPB):
        await FallingEdge(d.clk)
        d.tx_start.value, d.tx_data.value = (k % 7 == 0), 0xFF
    d.tx_start.value = 0
    await tb.idle()
    await tb.expect([0x5A])


@cocotb.test()
async def data_latched(dut):
    """send() changes tx_data the cycle after tx_start; the frame keeps the old byte."""
    tb = await new_bench(dut)
    await tb.send(0xC3)
    await tb.expect([0xC3])


@cocotb.test()
async def back_to_back(dut):
    tb = await new_bench(dut)
    d = dut
    rng = random.Random(int(os.environ.get("SEED", "1")))
    data = [rng.getrandbits(8) for _ in range(32 if FAST else 3)]
    sent = 0
    d.tx_start.value, d.tx_data.value = 1, data[0]
    while sent < len(data):
        await FallingEdge(d.clk)
        if int(d.tx_busy.value) and int(d.tx_start.value):
            sent += 1                      # the byte on tx_data was taken last edge
            if sent < len(data):
                d.tx_data.value = data[sent]
                # hold tx_start low while busy, raise it again in the IDLE cycle
                d.tx_start.value = 0
        elif not int(d.tx_busy.value):
            d.tx_start.value = 1
    d.tx_start.value = 0
    await tb.idle()
    await tb.expect(data)


@cocotb.test()
async def reset_mid_frame(dut):
    tb = await new_bench(dut)
    d = dut
    await tb.send(0x00, wait=False)
    for _ in range(3 * CPB):
        await FallingEdge(d.clk)
    d.rst.value = 1
    await FallingEdge(d.clk)
    assert int(d.tx.value) == 1 and int(d.tx_busy.value) == 0, "reset: line high, not busy"
    d.rst.value = 0
    tb.errors.clear()                      # the cut frame is a short run, expected
    for _ in range(12 * CPB):
        await FallingEdge(d.clk)
    tb.frames.clear(); tb.errors.clear()
    await tb.send(0x99)
    await tb.expect([0x99])


@cocotb.test(skip=not FAST)
async def loopback(dut):
    """Sample tx the way uart_rx does (1.5 bit times after the start edge, then
    every bit time) and compare."""
    tb = await new_bench(dut)
    d = dut
    rng = random.Random(int(os.environ.get("SEED", "1")) + 9)
    for _ in range(40):
        b = rng.getrandbits(8)
        await FallingEdge(d.clk)
        d.tx_start.value, d.tx_data.value = 1, b
        await FallingEdge(d.clk)
        d.tx_start.value = 0
        while int(d.tx.value) == 1:
            await FallingEdge(d.clk)
        for _ in range(CPB + CPB // 2):
            await FallingEdge(d.clk)
        got = 0
        for i in range(8):
            got |= int(d.tx.value) << i
            for _ in range(CPB):
                await FallingEdge(d.clk)
        assert int(d.tx.value) == 1, "stop bit"
        assert got == b, f"mid-bit sampled {got:#04x}, sent {b:#04x}"
        await tb.idle()
    await tb.expect(tb.frames[:])


def test_uart_tx():
    """pytest entry point: a fast build and the real 100 MHz / 115200 one."""
    for name, clk, baud in (("fast", 1_600_000, 100_000), ("real", 100_000_000, 115_200)):
        os.environ["UART_CPB"] = str(clk // baud)
        run("uart_tx", ["rtl/periph/uart_tx.sv"], "uart_tx", "test_uart_tx",
            parameters={"CLK_FREQ": clk, "BAUD_RATE": baud}, tag=f"_{name}")
