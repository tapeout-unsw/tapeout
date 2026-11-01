"""
B7: bus-to-core checks on the whole dual-core chip (rtl/soc_top.sv).

Run: pytest sim/cocotb/integration/test_soc_bus.py   (or `make test-cocotb`)
SEED=<n> reproduces a run; the seed used is printed at the start.

Nothing is modelled here: the program goes in over the uart_rx pin, through
uart_rx and the bootloader into sram_mem; both cores then fetch it through
their own I-caches over the shared bus, and results come out on the uart_tx
pin. The same cycle-by-cycle bus monitor as test_core_bus.py (B1-B6) watches
the real arbiter / addr_decode nets inside soc_top the whole time, so every
grant, every forwarded request and every read returned on bus_rdata is checked
against the reference model.

The program (prog_lock in test_core_bus.py, shared by both cores):
  CORE_ID picks each core's slot, both cores increment a shared counter n
  times under LOCK, each prints its id under LOCK, core 0 waits for both to
  finish and prints the counter, both write EXIT -> `done`.
"""

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from test_core_bus import RAW, Bench, prog_lock

CPB = 8                     # UART cycles per bit: CLK_FREQ / BAUD_RATE below
N = 20                      # lock iterations per core
COUNTER, FINISHED = 0x300, 0x304

# harness signal names (used by Bench) -> soc_top net names
SOC_NAMES = {
    "arb_loading": "loading",
    "arb_boot_req": "boot_req",
    "arb_boot_addr": "boot_addr",
    "arb_boot_wdata": "boot_wdata",
    "arb_boot_wstrb": "boot_wstrb",
    "retire0": "c0_retire", "retire_pc0": "c0_retire_pc",
    "retire1": "c1_retire", "retire_pc1": "c1_retire_pc",
}


async def uart_send(dut, data):
    """drive bytes on the uart_rx pin, 8N1, CPB cycles per bit."""
    for byte in data:
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
        for b in bits:
            dut.uart_rx.value = b
            for _ in range(CPB):
                await RisingEdge(dut.clk)
        for _ in range(2):                      # a little idle between bytes
            await RisingEdge(dut.clk)


@cocotb.test()
async def b7_soc_top_end_to_end(dut):
    """B7: load over UART, run both cores from RAM through the I-caches,
    check the shared-bus protocol throughout and the UART output at the end."""
    tb = Bench(dut, names=SOC_NAMES)
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.uart_rx.value = 1
    dut.rst.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    cocotb.start_soon(tb.monitor())

    prog, _, expected = prog_lock(N, counter=COUNTER, finished=FINISHED)
    nwords = max(max(expected) // 4 + 1, len(prog))
    image = prog + [0] * (nwords - len(prog))   # code at 0, zeroed data after it
    assert len(prog) * 4 <= 0x100, "program would overlap the data slots"

    uart = []
    cocotb.start_soon(tb.uart_listen(dut.uart_tx, CPB, uart))

    stream = list(nwords.to_bytes(4, "little"))
    for w in image:
        stream += list(w.to_bytes(4, "little"))
    await uart_send(dut, stream)

    for _ in range(100):
        await RisingEdge(dut.clk)
    assert int(dut.core_run.value) == 1, "bootloader never released the cores"
    assert tb.grants["boot"] == nwords, f"bootloader wrote {tb.grants['boot']} words, expected {nwords}"

    for _ in range(100_000):
        await RisingEdge(dut.clk)
        if int(dut.done.value) and len(uart) >= 3:
            break
    else:
        raise AssertionError(f"timeout: done={int(dut.done.value)} uart={uart} "
                             f"grants={tb.grants} first errors={tb.errors[:3]}")

    bad = []
    if sorted(uart[:2]) != [0x30, 0x31]:
        bad.append(f"first two UART bytes {uart[:2]}, expected '0' and '1' in either order")
    if uart[2] != 2 * N:
        bad.append(f"core 0 printed counter {uart[2]}, expected {2 * N} "
                   "(lost updates mean LOCK wasn't atomic over the bus)")
    # the reference model has seen every bus write; the monitor checked every read
    for addr, want in expected.items():
        got = tb.ram.get(addr >> 2)
        if got != want:
            bad.append(f"RAM @0x{addr:03x} = {got}, expected {want}")
    for k in ("d0", "d1", "ic0", "ic1"):
        if tb.grants[k] == 0:
            bad.append(f"{k} was never granted - is it connected?")
    tb.finish(bad)


# --------------------------------------------------------------------------
# pytest entry point (picked up by `make test-cocotb`)
# --------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

SOC_SOURCES = [
    "rtl/core/alu.sv",
    "rtl/core/decoder.sv",
    "rtl/core/imm_gen.sv",
    "rtl/core/mem_access.sv",
    "rtl/core/register_file.sv",
    "rtl/core/core.sv",
    "rtl/cache/icache_storage.sv",
    "rtl/cache/icache_lru.sv",
    "rtl/cache/icache_FSM.sv",
    "rtl/cache/icache.sv",
    "rtl/bus/arbiter.sv",
    "rtl/bus/addr_decode.sv",
    "rtl/mem/sram_macro.sv",
    "rtl/mem/sram_mem.sv",
    "rtl/periph/uart_rx.sv",
    "rtl/periph/uart_tx.sv",
    "rtl/periph/mmio.sv",
    "rtl/bootloader.sv",
    "rtl/soc_top.sv",
]


def test_soc_bus():
    from runner import run
    run("soc_bus", SOC_SOURCES, "soc_top", "test_soc_bus",
        parameters={"RAW": RAW, "CLK_FREQ": CPB, "BAUD_RATE": 1})
