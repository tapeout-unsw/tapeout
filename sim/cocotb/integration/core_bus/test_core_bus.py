"""
Bus-to-core integration tests (B1-B6).

Run: pytest sim/cocotb/integration/test_core_bus.py   (or `make test-cocotb`)
SEED=<n> reproduces a run; the seed used is printed at the start of each test.

DUT: two real `core`s -> `arbiter` -> `addr_decode` -> `sram_mem` / `mmio`
-> `uart_tx`, plus the real `bootloader` for B6 (see core_bus_harness.sv).
B7, the same checks on the whole chip (soc_top), is in test_soc_bus.py.
The I-cache refill ports are driven from Python to create contention, and the
boot port is driven from Python in B1-B5 (it is also how RAM is preloaded and
read back, so the test never reaches inside the SRAM macros).

Each core runs a small RV32E program from its own instruction ROM and writes
its results into its own region of SRAM. At the end we read SRAM back over the
bus (boot-port reads while loading=1) and compare. While the programs run, a cycle-by-cycle bus monitor
checks the protocol:

  * at most one grant per cycle, and only to a source that is requesting
  * b_valid / b_src / b_addr / b_wdata / b_wstrb match the winning request
  * no core or I-cache grant while `loading` is high; boot always wins then
  * a request that is not granted holds addr/wdata/wstrb stable next cycle
  * round-robin fairness: no requester waits more than 3 cycles
  * addr_decode routes RAM vs MMIO correctly (s_en/s_addr/m_en/m_addr/m_core)
  * bus_rdata, one cycle after every read grant, equals the reference model

  B1  single core: sw/sh/sb + lw/lh/lhu/lb/lbu through the bus
  B2  both cores run B1 at once, random fetch stalls to vary alignment
  B3  both cores loop load-modify-store while ic0/ic1 hammer the bus
  B4  bootloader stage: cores are held off while boot writes their data
  B5  real mmio: CORE_ID per core, LOCK-protected shared counter, UART, EXIT/done
  B6  real bootloader: bytes in on rx -> image written over the bus -> cores
      held in reset until core_run, then run on the loaded data
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

RAW = 11                       # 2 KiB RAM; keep in sync with the harness / SoC
SWORDS = 1 << (RAW - 2)
MMIO_BASE = 0x1000
MAX_WAIT = 3                   # 4 round-robin requesters -> wait <= 3 cycles
SRC = {0: "boot", 1: "d0", 2: "d1", 3: "ic0", 4: "ic1", 5: "none"}

# --------------------------------------------------------------------------
# Tiny RV32 assembler (just what these programs need)
# --------------------------------------------------------------------------
def _i(op, rd, f3, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op

def _s(f3, rs1, rs2, imm):
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | 0x23

def _b(f3, rs1, rs2, off):
    off &= 0x1FFF
    return (((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) | (rs2 << 20) | (rs1 << 15) \
        | (f3 << 12) | (((off >> 1) & 0xF) << 8) | (((off >> 11) & 1) << 7) | 0x63

def addi(rd, rs1, imm): return _i(0x13, rd, 0, rs1, imm)
def add(rd, rs1, rs2):  return (rs2 << 20) | (rs1 << 15) | (rd << 7) | 0x33
def lui(rd, imm20):     return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | 0x37
def lb(rd, rs1, imm):   return _i(0x03, rd, 0, rs1, imm)
def lh(rd, rs1, imm):   return _i(0x03, rd, 1, rs1, imm)
def lw(rd, rs1, imm):   return _i(0x03, rd, 2, rs1, imm)
def lbu(rd, rs1, imm):  return _i(0x03, rd, 4, rs1, imm)
def lhu(rd, rs1, imm):  return _i(0x03, rd, 5, rs1, imm)
def sb(rs2, rs1, imm):  return _s(0, rs1, rs2, imm)
def sh(rs2, rs1, imm):  return _s(1, rs1, rs2, imm)
def sw(rs2, rs1, imm):  return _s(2, rs1, rs2, imm)
def bne(rs1, rs2, off): return _b(1, rs1, rs2, off)
HALT = 0x0000006F               # jal x0, 0  (spin forever, no data access)

def li(rd, val):
    val &= 0xFFFFFFFF
    hi = ((val + 0x800) >> 12) & 0xFFFFF
    lo = (val - (hi << 12)) & 0xFFF
    return [lui(rd, hi), addi(rd, rd, lo)]

def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v

# --------------------------------------------------------------------------
# Programs. Each returns (words, preload{byte_addr: word}, expected{byte_addr: word})
# --------------------------------------------------------------------------
LOAD_PATTERN = 0x80F17F81       # bytes 81 7F F1 80: mixes sign bits per lane

def prog_widths(base, v):
    p = [addi(1, 0, base), *li(2, v),
         sw(2, 1, 0), lw(3, 1, 0), sw(3, 1, 4),        # word store + load-back
         sb(2, 1, 13), sh(2, 1, 18),                   # sub-word stores into preloaded words
         lb(4, 1, 32),  sw(4, 1, 64),
         lbu(4, 1, 33), sw(4, 1, 68),
         lb(4, 1, 34),  sw(4, 1, 72),
         lbu(4, 1, 35), sw(4, 1, 76),
         lh(4, 1, 32),  sw(4, 1, 80),
         lh(4, 1, 34),  sw(4, 1, 84),
         lhu(4, 1, 34), sw(4, 1, 88),
         lw(5, 1, 12),  sw(5, 1, 92),                  # read back the sb result
         HALT]
    pre = {base + 12: 0xAAAAAAAA, base + 16: 0xBBBBBBBB, base + 32: LOAD_PATTERN}
    w12 = (0xAAAAAAAA & ~0xFF00) | ((v & 0xFF) << 8)
    exp = {
        base + 0: v, base + 4: v,
        base + 12: w12,
        base + 16: ((v & 0xFFFF) << 16) | 0xBBBB,
        base + 64: sext(0x81, 8) & 0xFFFFFFFF,
        base + 68: 0x7F,
        base + 72: sext(0xF1, 8) & 0xFFFFFFFF,
        base + 76: 0x80,
        base + 80: 0x7F81,
        base + 84: sext(0x80F1, 16) & 0xFFFFFFFF,
        base + 88: 0x80F1,
        base + 92: w12,
    }
    return p, pre, exp

def prog_loop(base, n):
    """counter at base+0 incremented n times (lw/addi/sw), array[i]=i at
    base+4.., running sum of the read-back array at base+0xFC."""
    p = [addi(1, 0, base), addi(6, 0, n), addi(7, 0, 0),
         addi(9, 1, 4), addi(11, 0, 0)]
    loop = len(p)
    p += [lw(3, 1, 0), addi(3, 3, 1), sw(3, 1, 0),
          sw(7, 9, 0), lw(10, 9, 0), add(11, 11, 10),
          addi(9, 9, 4), addi(7, 7, 1)]
    p += [bne(7, 6, (loop - len(p)) * 4)]
    p += [sw(11, 1, 0xFC), HALT]
    exp = {base: n, base + 0xFC: n * (n - 1) // 2}
    exp.update({base + 4 + 4 * i: i for i in range(n)})
    return p, {}, exp

def beq(rs1, rs2, off): return _b(0, rs1, rs2, off)

def assemble(items):
    """items: instruction words, label strings, or ("bne"/"beq", rs1, rs2, label)."""
    labels, pc = {}, 0
    for it in items:
        if isinstance(it, str):
            labels[it] = pc
        else:
            pc += 4
    out, pc = [], 0
    for it in items:
        if isinstance(it, str):
            continue
        if isinstance(it, tuple):
            op, rs1, rs2, lab = it
            out.append((bne if op == "bne" else beq)(rs1, rs2, labels[lab] - pc))
        else:
            out.append(it)
        pc += 4
    return out

UART_DATA, UART_STATUS, LOCK, CORE_ID, EXIT = 0x0, 0x4, 0x8, 0xC, 0x10

def prog_lock(n, counter=0x300, finished=None):
    """Same program on both cores, which tell themselves apart with CORE_ID.
      * store CORE_ID at 0x100 (core 0) / 0x200 (core 1)
      * n times: take LOCK (spin on test-and-set), counter += 1, release
      * under LOCK: wait for the UART, print '0' + CORE_ID
      * write EXIT, read EXIT back into base+4
    With `finished`: each core also bumps a finished count under the lock,
    and core 0 then waits for both and prints the counter's low byte.
    Without the lock the lw/addi/sw on the counter races and loses updates."""
    x = [lui(1, MMIO_BASE >> 12),
         lw(2, 1, CORE_ID),
         addi(5, 0, 0x200), ("bne", 2, 0, "id1"), addi(5, 0, 0x100), "id1",
         sw(2, 5, 0),
         addi(6, 0, n), addi(7, 0, 0), addi(8, 0, counter),
         "acq", lw(3, 1, LOCK), ("bne", 3, 0, "acq"),
         lw(4, 8, 0), addi(4, 4, 1), sw(4, 8, 0),
         sw(0, 1, LOCK),
         addi(7, 7, 1), ("bne", 7, 6, "acq"),
         "acq2", lw(3, 1, LOCK), ("bne", 3, 0, "acq2"),
         "poll", lw(3, 1, UART_STATUS), ("bne", 3, 0, "poll"),
         addi(10, 2, 0x30), sw(10, 1, UART_DATA)]
    if finished is not None:
        x += [addi(9, 0, finished), lw(4, 9, 0), addi(4, 4, 1), sw(4, 9, 0)]
    x += [sw(0, 1, LOCK)]
    if finished is not None:
        x += [("bne", 2, 0, "exit"),
              addi(12, 0, 2), "wait", lw(4, 9, 0), ("bne", 4, 12, "wait"),
              "poll2", lw(3, 1, UART_STATUS), ("bne", 3, 0, "poll2"),
              lw(10, 8, 0), sw(10, 1, UART_DATA),
              "exit"]
    x += [sw(0, 1, EXIT), lw(11, 1, EXIT), sw(11, 5, 4), "halt", HALT]
    p = assemble(x)
    exp = {0x100: 0, 0x200: 1, counter: 2 * n}
    if finished is not None:
        exp[finished] = 2
    return p, {}, exp

# --------------------------------------------------------------------------
# Bench
# --------------------------------------------------------------------------
class Bench:
    def __init__(self, dut, names=None):
        self.dut = dut
        self.names = names or {}           # harness signal name -> name in this DUT
        self.ram = {}                      # word index -> value (reference model)
        self.lock, self.exit = 0, 0        # mmio.sv state
        self.uart_writes = []              # (core, byte) written to UART_DATA
        self.errors = []
        self.grants = {k: 0 for k in SRC.values()}
        self.max_wait = {k: 0 for k in ("d0", "d1", "ic0", "ic1")}
        self.bus_log = []                  # (cycle, src, addr, wstrb, wdata)
        self.cycle = 0
        self.halted = [False, False]
        self.retired = [0, 0]
        seed = int(os.environ.get("SEED", random.randrange(1 << 32)))
        random.seed(seed)
        dut._log.info(f"SEED={seed}")

    def sig(self, name):
        return getattr(self.dut, self.names.get(name, name))

    def err(self, msg):
        if len(self.errors) < 20:
            self.dut._log.error(f"[cycle {self.cycle}] {msg}")
        self.errors.append(msg)

    async def start(self, loading=0):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 10, unit="ns").start())
        for s in ("boot_req", "ic0_req", "ic1_req", "rom_we", "use_bl", "rx_valid"):
            getattr(d, s).value = 0
        for s in ("boot_addr", "boot_wdata", "boot_wstrb", "ic0_addr", "ic1_addr",
                  "rom_sel", "rom_waddr", "rom_wdata", "rx_data"):
            getattr(d, s).value = 0
        d.i_go0.value = 1
        d.i_go1.value = 1
        d.loading.value = 1          # boot owns the bus while we preload
        d.core_hold.value = 1
        d.rst.value = 1
        await RisingEdge(d.clk)

    async def load(self, core, words):
        d = self.dut
        prog = list(words) + [HALT] * (256 - len(words))
        for a, w in enumerate(prog):
            d.rom_we.value, d.rom_sel.value, d.rom_waddr.value, d.rom_wdata.value = 1, core, a, w
            await RisingEdge(d.clk)
        d.rom_we.value = 0
        return (len(words) - 1) * 4           # halt PC = last word

    async def boot_write(self, addr, w):
        """one boot-port write (call right after a rising edge)."""
        d = self.dut
        d.boot_req.value, d.boot_addr.value = 1, addr
        d.boot_wdata.value, d.boot_wstrb.value = w, 0xF
        await RisingEdge(d.clk)
        d.boot_req.value = 0

    async def preload(self, pre):
        """write words over the bus via the boot port (loading must be 1);
        the monitor sees these writes, so the reference model follows."""
        for addr, w in pre.items():
            await self.boot_write(addr, w)

    async def zero_ram(self):
        await self.preload({4 * i: 0 for i in range(SWORDS)})

    async def release(self):
        """leave reset with the cores held and the boot port owning the bus.
        Inputs change just after a rising edge (like flop outputs) so the
        monitor, which samples on the falling edge, sees each cycle settled."""
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        await RisingEdge(self.dut.clk)

    async def run_cores(self, loading=0):
        """release the cores (call right after a rising edge)."""
        self.dut.loading.value = loading
        self.dut.core_hold.value = 0
        await RisingEdge(self.dut.clk)

    async def peek(self, addr):
        """read one word over the bus with a boot-port read (loading=1)."""
        d = self.dut
        d.boot_req.value, d.boot_addr.value, d.boot_wstrb.value = 1, addr, 0
        await RisingEdge(d.clk)              # granted at this edge
        d.boot_req.value = 0
        await FallingEdge(d.clk)             # rdata valid the cycle after
        v = d.bus_rdata.value
        await RisingEdge(d.clk)
        return int(v) if v.is_resolvable else None

    # ---- the cycle-by-cycle bus checker ---------------------------------
    async def monitor(self):
        d = self.dut
        req_names = ("d0", "d1", "ic0", "ic1")
        waiting = {k: 0 for k in req_names}
        prev_core_req = {0: None, 1: None}
        pending_read = None                    # (expected, src, addr)
        atomic_owner = None                    # "d0"/"d1" while its amoswap holds the bus
        while True:
            await FallingEdge(d.clk)
            self.cycle += 1
            if int(d.rst.value):
                continue
            g = lambda s: int(self.sig(s).value)

            # ---- read data returned this cycle
            if pending_read is not None:
                exp, src, addr = pending_read
                got = int(d.bus_rdata.value) if d.bus_rdata.value.is_resolvable else None
                if got != exp:
                    self.err(f"bus_rdata for {src} read @0x{addr:04x}: got "
                             f"{'X' if got is None else hex(got)}, expected 0x{exp:08x}")
                pending_read = None

            loading = g("arb_loading")
            req = {"d0": g("d0_req"), "d1": g("d1_req"), "ic0": g("ic0_req"), "ic1": g("ic1_req")}
            gnt = {"d0": g("d0_gnt"), "d1": g("d1_gnt"), "ic0": g("ic0_gnt"), "ic1": g("ic1_gnt")}
            b_valid, b_src = g("b_valid"), g("b_src")
            b_addr, b_wstrb, b_wdata = g("b_addr"), g("b_wstrb"), g("b_wdata")
            locked = atomic_owner is not None

            # ---- one grant max, only to requesters
            if sum(gnt.values()) > 1:
                self.err(f"multiple grants {gnt}")
            for k in req_names:
                if gnt[k] and not req[k]:
                    self.err(f"{k}_gnt without {k}_req")

            # ---- loading stage
            if loading:
                if any(gnt.values()):
                    self.err(f"grant {gnt} while loading=1")
                if b_valid != g("arb_boot_req") or (b_valid and b_src != 0):
                    self.err(f"loading: b_valid={b_valid} b_src={b_src} boot_req={g('arb_boot_req')}")
            else:
                if locked:
                    if b_valid != req[atomic_owner]:
                        self.err(f"b_valid={b_valid} while {atomic_owner}'s amoswap holds the bus, "
                                 f"requests={req}")
                    for k in req_names:
                        if gnt[k] and k != atomic_owner:
                            self.err(f"{k} granted while {atomic_owner}'s amoswap holds the bus")
                elif b_valid != int(any(req.values())):
                    self.err(f"b_valid={b_valid} but requests={req}")
                won = [k for k in req_names if gnt[k]]
                if b_valid and (not won or SRC.get(b_src) != won[0]):
                    self.err(f"b_src={b_src} ({SRC.get(b_src)}) disagrees with grants {gnt}")

            # ---- forwarded fields match the winner
            if b_valid:
                src = SRC.get(b_src, "?")
                if src == "boot":
                    want = (g("arb_boot_addr"), g("arb_boot_wstrb"), g("arb_boot_wdata"))
                elif src in ("d0", "d1"):
                    want = (g(f"{src}_addr"), g(f"{src}_wstrb"), g(f"{src}_wdata"))
                else:
                    want = (g(f"{src}_addr"), 0, 0)
                if (b_addr, b_wstrb, b_wdata) != want:
                    self.err(f"{src} forwarded as addr/wstrb/wdata="
                             f"{b_addr:#x}/{b_wstrb:#x}/{b_wdata:#x}, request was "
                             f"{want[0]:#x}/{want[1]:#x}/{want[2]:#x}")
                self.grants[src] = self.grants.get(src, 0) + 1
                self.bus_log.append((self.cycle, src, b_addr, b_wstrb, b_wdata))
                pending_read = self._apply(b_addr, b_wstrb, b_wdata, b_src, src)
            else:
                self.grants["none"] += 1

            # ---- amoswap bus lock (arbiter.sv): a core's atomic read takes the
            # bus, and the next granted write (the swap's write) releases it
            if b_valid and not locked and SRC.get(b_src) in ("d0", "d1") \
                    and b_wstrb == 0 and g(f"{SRC[b_src]}_atomic"):
                atomic_owner = SRC[b_src]
            elif b_valid and locked and b_wstrb != 0:
                atomic_owner = None

            # ---- decoder routing
            self._check_decode(b_valid, b_addr, b_wstrb, b_wdata, b_src)

            # ---- core requests held stable until granted
            for c in (0, 1):
                k = f"d{c}"
                cur = (g(f"{k}_addr"), g(f"{k}_wstrb"), g(f"{k}_wdata")) if req[k] else None
                prev = prev_core_req[c]
                if prev is not None and cur != prev:
                    self.err(f"{k} dropped/changed an ungranted request: {prev} -> {cur}")
                prev_core_req[c] = cur if (req[k] and not gnt[k]) else None

            # ---- fairness
            for k in req_names:
                if locked and req[k] and not gnt[k]:
                    continue                   # waiting behind an amoswap: pause the count
                if req[k] and not gnt[k] and not loading:
                    waiting[k] += 1
                    if waiting[k] > MAX_WAIT:
                        self.err(f"{k} waited {waiting[k]} cycles (round-robin bound {MAX_WAIT})")
                else:
                    self.max_wait[k] = max(self.max_wait[k], waiting[k])
                    waiting[k] = 0

            # ---- core progress
            for c in (0, 1):
                if g(f"retire{c}"):
                    self.retired[c] += 1
                    if getattr(self, "halt_pc", None) and g(f"retire_pc{c}") == self.halt_pc[c]:
                        self.halted[c] = True

    def _apply(self, addr, wstrb, wdata, b_src, src):
        """update the reference model; return expected read data if a read."""
        if addr & MMIO_BASE:                   # mirrors mmio.sv
            core = 1 if b_src == 2 else 0
            idx = (addr >> 2) & 7
            if wstrb:
                if idx == 0:
                    self.uart_writes.append((core, wdata & 0xFF))
                elif idx == 2:
                    self.lock = 0
                elif idx == 4:
                    self.exit |= 1 << core
                return None
            if idx == 1:
                return None                    # UART_STATUS: timing-dependent, not checked
            val = {2: self.lock, 3: core, 4: self.exit, 5: self.lock}.get(idx, 0)
            if idx == 2:
                self.lock = 1                  # test-and-set
            return (val, src, addr)
        w = (addr >> 2) & (SWORDS - 1)
        old = self.ram.get(w, 0)
        if wstrb:
            new = old
            for b in range(4):
                if wstrb >> b & 1:
                    new = (new & ~(0xFF << 8 * b)) | (wdata & (0xFF << 8 * b))
            self.ram[w] = new
            return None
        return (old, src, addr)

    def _check_decode(self, b_valid, b_addr, b_wstrb, b_wdata, b_src):
        d = self.dut
        g = lambda s: int(self.sig(s).value)
        is_m = bool(b_addr & MMIO_BASE)
        s_en, m_en = g("s_en"), g("m_en")
        if s_en != int(b_valid and not is_m) or m_en != int(b_valid and is_m):
            self.err(f"addr_decode: addr {b_addr:#06x} b_valid={b_valid} gave s_en={s_en} "
                     f"m_en={m_en} (is addr_decode still the stub?)")
            return
        if s_en and (g("s_addr") != (b_addr >> 2) & (SWORDS - 1)
                     or g("s_wstrb") != b_wstrb or g("s_wdata") != b_wdata):
            self.err(f"addr_decode SRAM fields wrong for addr {b_addr:#06x}")
        if m_en:
            want = ((b_addr >> 2) & 7, int(b_wstrb != 0), b_wdata & 0xFF, int(b_src == 2))
            got = (g("m_addr"), g("m_we"), g("m_wdata"), g("m_core"))
            if got != want:
                self.err(f"addr_decode MMIO (m_addr,m_we,m_wdata,m_core) = {got}, expected {want}")

    # ---- traffic generators -------------------------------------------
    async def ic_traffic(self, port, density):
        """I-cache refill reads: raise req, hold until granted, then maybe re-request."""
        d = self.dut
        req, addr, gnt = (getattr(d, f"ic{port}_{s}") for s in ("req", "addr", "gnt"))
        while True:
            await FallingEdge(d.clk)
            accepted = int(req.value) and int(gnt.value)
            busy = int(req.value) and not accepted
            await RisingEdge(d.clk)
            if busy:
                continue
            if random.random() < density:
                req.value = 1
                addr.value = random.randrange(SWORDS) * 4
            else:
                req.value = 0

    async def uart_listen(self, pin, cpb, out):
        """decode 8N1 bytes on `pin` (cpb clock cycles per bit) into `out`."""
        clk = self.dut.clk
        while True:
            await RisingEdge(clk)
            if int(pin.value):
                continue
            for _ in range(cpb + cpb // 2):      # to the middle of data bit 0
                await RisingEdge(clk)
            b = 0
            for i in range(8):
                b |= int(pin.value) << i
                for _ in range(cpb):
                    await RisingEdge(clk)
            if not int(pin.value):
                self.err("UART framing error (stop bit low)")
            out.append(b)

    async def fetch_jitter(self, p_stall):
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            d.i_go0.value = int(random.random() >= p_stall)
            d.i_go1.value = int(random.random() >= p_stall)

    async def run_until_halt(self, halt_pc, timeout=5000):
        self.halt_pc = halt_pc
        for _ in range(timeout):
            await FallingEdge(self.dut.clk)
            if all(self.halted):
                return
        raise AssertionError(f"timeout: halted={self.halted} retired={self.retired} "
                             f"(first errors: {self.errors[:3]})")

    async def check_ram(self, expected, tag):
        """stop the cores' data traffic (loading=1), read back over the bus."""
        d = self.dut
        await RisingEdge(d.clk)
        d.use_bl.value = 0
        d.loading.value = 1
        await RisingEdge(d.clk)
        bad = []
        for addr, want in sorted(expected.items()):
            got = await self.peek(addr)
            if got != want & 0xFFFFFFFF:
                bad.append(f"{tag} @0x{addr:03x}: got {got if got is None else hex(got)}"
                           f" expected 0x{want & 0xFFFFFFFF:08x}")
        for b in bad:
            d._log.error(b)
        return bad

    async def check_whole_ram(self):
        """every word must agree with the bus-level reference model (the
        monitor flags any mismatch on each read)."""
        for w in range(SWORDS):
            await self.peek(4 * w)
        return []

    def finish(self, bad=()):
        d = self.dut
        d._log.info(f"grants: {self.grants}")
        d._log.info(f"max wait (cycles): {self.max_wait}")
        d._log.info(f"retired: core0={self.retired[0]} core1={self.retired[1]}")
        problems = list(self.errors) + list(bad)
        assert not problems, f"{len(problems)} problem(s); first: {problems[0]}"


async def setup(dut, prog0, prog1):
    """reset, load both ROMs, then (cores held, loading=1) zero the RAM and
    write each program's input data over the boot port."""
    tb = Bench(dut)
    await tb.start()
    h0 = await tb.load(0, prog0[0])
    h1 = await tb.load(1, prog1[0])
    cocotb.start_soon(tb.monitor())
    await tb.release()
    await tb.zero_ram()
    await tb.preload({**prog0[1], **prog1[1]})
    return tb, [h0, h1]

# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
@cocotb.test()
async def b1_single_core_widths(dut):
    """B1: core 0 alone - every load/store width through arbiter + decode."""
    p0 = prog_widths(0x100, 0x12345678)
    p1 = ([HALT], {}, {})
    tb, halt = await setup(dut, p0, p1)
    await tb.run_cores()
    await tb.run_until_halt(halt)
    bad = await tb.check_ram(p0[2], "core0") + await tb.check_whole_ram()
    assert tb.grants["d1"] == 0, "core 1 never touches memory in B1"
    tb.finish(bad)


@cocotb.test()
async def b2_dual_core_widths(dut):
    """B2: both cores run the width program at once with random fetch stalls."""
    p0 = prog_widths(0x100, 0x12345678)
    p1 = prog_widths(0x200, 0x89ABCDEF)
    tb, halt = await setup(dut, p0, p1)
    cocotb.start_soon(tb.fetch_jitter(0.3))
    await tb.run_cores()
    await tb.run_until_halt(halt)
    bad = (await tb.check_ram(p0[2], "core0") + await tb.check_ram(p1[2], "core1")
           + await tb.check_whole_ram())
    tb.finish(bad)


@cocotb.test()
async def b3_contention_with_icache_traffic(dut):
    """B3: both cores loop load-modify-store while ic0/ic1 keep the bus busy."""
    n = 20
    p0, p1 = prog_loop(0x100, n), prog_loop(0x200, n)
    tb, halt = await setup(dut, p0, p1)
    cocotb.start_soon(tb.ic_traffic(0, 0.7))
    cocotb.start_soon(tb.ic_traffic(1, 0.7))
    cocotb.start_soon(tb.fetch_jitter(0.2))
    await tb.run_cores()
    await tb.run_until_halt(halt, timeout=20000)
    bad = await tb.check_ram(p0[2], "core0") + await tb.check_ram(p1[2], "core1")
    assert tb.grants["ic0"] > 50 and tb.grants["ic1"] > 50, f"not enough contention: {tb.grants}"
    tb.finish(bad)


@cocotb.test()
async def b4_loading_holds_cores_off(dut):
    """B4: cores running while loading=1 stall on their first data access
    (and so do I-cache refills) while boot writes their input data; dropping
    loading lets them finish on that data."""
    p0 = prog_widths(0x100, 0x12345678)
    p1 = prog_widths(0x200, 0x89ABCDEF)
    boot_data = {**p0[1], **p1[1]}
    tb, halt = await setup(dut, (p0[0], {}, p0[2]), (p1[0], {}, p1[2]))
    d = dut
    cocotb.start_soon(tb.ic_traffic(0, 0.5))               # must also be held off
    await tb.run_cores(loading=1)
    for _ in range(10):                                    # let cores reach S_MEM
        await RisingEdge(d.clk)
    assert int(d.d0_req.value) and int(d.d1_req.value), "cores should be waiting on the bus"
    boots_before = tb.grants["boot"]
    for addr, w in boot_data.items():                      # boot writes, with gaps
        await tb.boot_write(addr, w)
        await RisingEdge(d.clk)
    retired_during_load = list(tb.retired)
    for _ in range(5):
        await RisingEdge(d.clk)
    assert tb.retired == retired_during_load, "a core retired while loading=1"
    assert tb.grants["d0"] == tb.grants["d1"] == tb.grants["ic0"] == 0, \
        f"something other than boot was granted while loading: {tb.grants}"
    assert tb.grants["boot"] - boots_before == len(boot_data)

    d.loading.value = 0
    await tb.run_until_halt(halt)
    bad = (await tb.check_ram(p0[2], "core0") + await tb.check_ram(p1[2], "core1")
           + await tb.check_whole_ram())
    tb.finish(bad)


@cocotb.test()
async def b5_mmio_lock_core_id_exit(dut):
    """B5: real mmio.sv. Both cores run the same program: CORE_ID tells them
    apart, a LOCK-protected counter must reach exactly 2n (the bus must keep
    LOCK's test-and-set atomic between cores), each prints its id on the UART
    under the lock, and `done` rises once both have written EXIT."""
    n = 20
    p = prog_lock(n)
    tb, halt = await setup(dut, p, p)
    uart = []
    cocotb.start_soon(tb.uart_listen(dut.uart_tx, 4, uart))
    cocotb.start_soon(tb.fetch_jitter(0.3))
    await tb.run_cores()
    await tb.run_until_halt(halt, timeout=20000)
    for _ in range(200):                                   # let the UART drain
        await RisingEdge(dut.clk)
    assert int(dut.done.value) == 1, "done did not rise after both cores wrote EXIT"
    bad = await tb.check_ram(p[2], "lock")
    ex0, ex1 = await tb.peek(0x104), await tb.peek(0x204)
    if not (ex0 & 1 and ex1 & 2 and 3 in (ex0, ex1)):
        bad.append(f"EXIT read-back core0={ex0} core1={ex1}: each must see its own bit, "
                   "and the later one must see both")
    if sorted(uart) != [0x30, 0x31]:
        bad.append(f"UART printed {[chr(b) for b in uart]}, expected '0' and '1'")
    if sorted(tb.uart_writes) != [(0, 0x30), (1, 0x31)]:
        bad.append(f"UART_DATA writes by core: {tb.uart_writes}")
    lock_reads = sum(1 for e in tb.bus_log if e[2] == MMIO_BASE + LOCK and e[3] == 0)
    dut._log.info(f"LOCK test-and-set reads: {lock_reads} (>{2 * n + 2} means real contention)")
    tb.finish(bad)


@cocotb.test()
async def b6_real_bootloader(dut):
    """B6: the real bootloader receives a word count + image as bytes, writes
    it over the bus from address 0, holds the cores in reset (core_run=0)
    until the last write has committed, then the cores run on that image."""
    p0 = prog_widths(0x100, 0x12345678)
    p1 = prog_widths(0x200, 0x89ABCDEF)
    data = {**p0[1], **p1[1]}
    nwords = max(data) // 4 + 1
    image = [data.get(4 * i, 0) for i in range(nwords)]
    tb, halt = await setup(dut, (p0[0], {}, p0[2]), (p1[0], {}, p1[2]))
    d = dut

    # hand the boot port to the bootloader; it sits in GET_LEN with loading=1
    d.core_hold.value = 0
    d.use_bl.value = 1
    await RisingEdge(d.clk)
    boots_before = tb.grants["boot"]

    stream = list(nwords.to_bytes(4, "little"))
    for w in image:
        stream += list(w.to_bytes(4, "little"))
    for byte in stream:
        d.rx_data.value, d.rx_valid.value = byte, 1
        await RisingEdge(d.clk)
        d.rx_valid.value = 0
        while random.random() < 0.3:                       # UART-like gaps
            await RisingEdge(d.clk)
        if int(d.core_run.value) == 0:
            assert tb.retired == [0, 0], "a core ran before core_run"

    for _ in range(4):
        await RisingEdge(d.clk)
    assert int(d.core_run.value) == 1, "bootloader never raised core_run"
    assert tb.grants["boot"] - boots_before == nwords, \
        f"bootloader made {tb.grants['boot'] - boots_before} writes, expected {nwords}"
    await tb.run_until_halt(halt)
    bad = (await tb.check_ram(p0[2], "core0") + await tb.check_ram(p1[2], "core1")
           + await tb.check_whole_ram())
    tb.finish(bad)


# --------------------------------------------------------------------------
# pytest entry point (picked up by `make test-cocotb`)
# --------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common"))   # sim/cocotb/common

SOURCES = [
    "rtl/core/alu.sv",
    "rtl/core/decoder.sv",
    "rtl/core/imm_gen.sv",
    "rtl/core/mem_access.sv",
    "rtl/core/register_file.sv",
    "rtl/core/core.sv",
    "rtl/core/amoswap.sv",
    "rtl/bus/arbiter.sv",
    "rtl/bus/addr_decode.sv",
    "rtl/mem/sram_macro.sv",
    "rtl/mem/sram_mem.sv",
    "rtl/bootloader.sv",
    "rtl/periph/mmio.sv",
    "rtl/periph/uart_tx.sv",
    # the harness next to this file, so the folder can be moved freely
    str((Path(__file__).resolve().parent / "core_bus_harness.sv")
        .relative_to(Path(__file__).resolve().parents[4])),   # repository root
]


def test_core_bus():
    from runner import run
    run("core_bus", SOURCES, "core_bus_harness", "test_core_bus", parameters={"RAW": RAW})
