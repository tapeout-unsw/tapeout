"""cocotb unit tests for rtl/core/core.sv (with its decoder, imm_gen,
register_file, alu, mem_access and amoswap).

The bench is the core's two ports only: Python serves the instruction port
and the data port from one 8 KiB memory, with a random number of stall
cycles before every i_valid and every d_gnt. A small instruction-set model
(ISS) runs the same program, and every run is compared on:
  - the sequence of retired PCs (retire / retire_pc),
  - the sequence of granted data accesses (word address, strobes, data),
  - the final memory, which includes a dump of x1..x15.
The bench also checks every cycle that a stalled request holds its address
and bundle, that d_atomic is high only for the amoswap read and write, and
that d_rdata is only sampled the cycle after a read grant (it carries garbage
otherwise).

  directed_isa          one program touching every instruction type
  stalls_every_state    the directed program with long i_valid and d_gnt stalls,
                        so S_FETCH, S_MEM and S_AMO_WAIT (both accesses) all wait
  random_programs       random programs, random stalls, amo_off = 0
  random_amo_off        the same with amo_off = 1: amoswap must be a no-op
                        (AMO-1); the ISS models it as one
  amo_d_atomic          d_atomic is high exactly for amoswap's two accesses
  unknown_opcodes       fence, ecall and undefined opcodes execute as no-ops
                        (documents current behaviour: there are no traps)
  reset_mid_program     rst at a random point restarts from pc 0 cleanly
  illegal_state_recovers  the state register forced to each unused encoding
                        (5, 6, 7) returns to S_FETCH and the program finishes
                        with the right results (the default arm of the FSM)

Built at NREGS = 16 and 32. Run with `make test-cocotb BLOCK=core`.
SEED=<n> sets the seed, PROGS=<n> the number of random programs.
"""

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import run  # noqa: E402

M = 0xFFFF_FFFF
NREGS = int(os.environ.get("CORE_NREGS", "16"))
WORDS = 2048                       # 13-bit byte address space
DATA = 0x800                       # loads/stores in DATA..DATA+0x3FF
DUMP = 0xF00                       # x1..x15 dumped here at the end
HALT = 0x0000_006F                 # jal x0, 0


# --------------------------------------------------------------------------
# assembler
# --------------------------------------------------------------------------
def _r(f7, rs2, rs1, f3, rd, op=0x33): return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op
def _i(op, rd, f3, rs1, imm): return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op
def _s(f3, rs1, rs2, imm):
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | 0x23
def _b(f3, rs1, rs2, off):
    off &= 0x1FFF
    return (((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) | (rs2 << 20) | (rs1 << 15) \
        | (f3 << 12) | (((off >> 1) & 0xF) << 8) | (((off >> 11) & 1) << 7) | 0x63
def _j(rd, off):
    off &= 0x1FFFFF
    return (((off >> 20) & 1) << 31) | (((off >> 1) & 0x3FF) << 21) | (((off >> 11) & 1) << 20) \
        | (((off >> 12) & 0xFF) << 12) | (rd << 7) | 0x6F
def addi(rd, rs1, imm): return _i(0x13, rd, 0, rs1, imm)
def lui(rd, imm20): return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | 0x37
def auipc(rd, imm20): return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | 0x17
def jalr(rd, rs1, imm): return _i(0x67, rd, 0, rs1, imm)
def sw(rs2, rs1, imm): return _s(2, rs1, rs2, imm)
def lw(rd, rs1, imm): return _i(0x03, rd, 2, rs1, imm)
def amoswap(rd, rs2, rs1, aqrl=0):
    return (0b00001 << 27) | (aqrl << 25) | (rs2 << 20) | (rs1 << 15) | (0b010 << 12) | (rd << 7) | 0x2F
def li(rd, val):
    val &= M
    hi = ((val + 0x800) >> 12) & 0xFFFFF
    return [lui(rd, hi), addi(rd, rd, (val - (hi << 12)) & 0xFFF)]


def s32(x): return x - (1 << 32) if x & 0x8000_0000 else x
def sext(v, n):
    v &= (1 << n) - 1
    return (v - (1 << n)) & M if v >> (n - 1) else v


# --------------------------------------------------------------------------
# instruction-set model
# --------------------------------------------------------------------------
def iss(mem, amo_off, nregs=NREGS, max_steps=20000):
    """Run from pc 0 until HALT. Returns (retired pcs, data accesses, memory)."""
    mem = list(mem)
    regs = [0] * 32
    pcs, acc = [], []
    pc = 0

    def rr(r): return 0 if r == 0 or r >= nregs else regs[r]
    def wr(r, v):
        if 0 < r < nregs: regs[r] = v & M

    for _ in range(max_steps):
        inst = mem[(pc & 0x1FFF) >> 2]
        pcs.append(pc)
        if inst == HALT:
            return pcs, acc, mem
        op, rd, f3 = inst & 0x7F, (inst >> 7) & 31, (inst >> 12) & 7
        rs1, rs2, f7 = (inst >> 15) & 31, (inst >> 20) & 31, inst >> 25
        a, b = rr(rs1), rr(rs2)
        i_imm = sext(inst >> 20, 12)
        nxt = (pc + 4) & M
        if op in (0x33, 0x13):
            if op == 0x13:
                b = i_imm
            sh = b & 31
            alt = (f7 & 0x20) != 0
            if f3 == 0:   v = a - b if (op == 0x33 and alt) else a + b
            elif f3 == 1: v = a << sh
            elif f3 == 2: v = int(s32(a) < s32(b))
            elif f3 == 3: v = int(a < (b & M))
            elif f3 == 4: v = a ^ b
            elif f3 == 5: v = (s32(a) >> sh) if alt else (a >> sh)
            elif f3 == 6: v = a | b
            else:         v = a & b
            wr(rd, v)
        elif op == 0x03:                                     # loads
            addr = (a + i_imm) & 0x1FFF
            w = mem[addr >> 2]; lo = addr & 3
            acc.append(("R", addr >> 2))
            byte, half = (w >> (8 * lo)) & 0xFF, (w >> (16 if lo & 2 else 0)) & 0xFFFF
            v = {0: sext(byte, 8), 1: sext(half, 16), 4: byte, 5: half}.get(f3, w)
            wr(rd, v)
        elif op == 0x23:                                     # stores
            addr = (a + sext(((inst >> 25) << 5) | ((inst >> 7) & 31), 12)) & 0x1FFF
            lo = addr & 3
            if f3 == 0:   strb, data = 1 << lo, (b & 0xFF) * 0x0101_0101
            elif f3 == 1: strb, data = (0xC if lo & 2 else 0x3), (b & 0xFFFF) * 0x0001_0001
            else:         strb, data = 0xF, b
            mask = sum(0xFF << (8 * k) for k in range(4) if strb >> k & 1)
            acc.append(("W", addr >> 2, strb, data & mask))
            mem[addr >> 2] = (mem[addr >> 2] & ~mask & M) | (data & mask)
        elif op == 0x63:                                     # branches
            off = sext((((inst >> 31) & 1) << 12) | (((inst >> 7) & 1) << 11)
                       | (((inst >> 25) & 0x3F) << 5) | (((inst >> 8) & 0xF) << 1), 13)
            take = {0: a == b, 1: a != b, 4: s32(a) < s32(b), 5: s32(a) >= s32(b),
                    6: a < b, 7: a >= b}.get(f3, False)
            if take: nxt = (pc + off) & M
        elif op == 0x37: wr(rd, inst & 0xFFFF_F000)
        elif op == 0x17: wr(rd, pc + (inst & 0xFFFF_F000))
        elif op == 0x6F:
            off = sext((((inst >> 31) & 1) << 20) | (((inst >> 12) & 0xFF) << 12)
                       | (((inst >> 20) & 1) << 11) | (((inst >> 21) & 0x3FF) << 1), 21)
            wr(rd, pc + 4); nxt = (pc + off) & M
        elif op == 0x67:
            nxt = (a + i_imm) & 0xFFFF_FFFE; wr(rd, pc + 4)
        elif op == 0x2F and f3 == 2 and (inst >> 27) == 1 and not amo_off:
            idx = (a & 0x1FFF) >> 2
            old = mem[idx]
            acc += [("R", idx), ("W", idx, 0xF, b)]
            mem[idx] = b
            wr(rd, old)
        # anything else: no-op (the core has no traps)
        pc = nxt
    raise AssertionError("ISS: program did not reach HALT")


# --------------------------------------------------------------------------
# random program generator
# --------------------------------------------------------------------------
AMO_PTR, BASE = 13, 14                 # reserved: amoswap pointer, data base


def gen_program(rng, n=150, unknown=False):
    def rd():
        r = rng.random()
        if r < 0.05: return 0
        if r < 0.10: return rng.randrange(16, 32)         # dropped at NREGS = 16
        return rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15])
    def rs():
        return rng.randrange(32) if rng.random() < 0.1 else rng.randrange(16)

    p = []
    for r in range(1, 32):          # registers have no reset; x16+ are dropped at NREGS = 16
        p += li(r, rng.choice([0, 1, M, 0x8000_0000, 0x7FFF_FFFF, rng.getrandbits(32), rng.getrandbits(32)]))
    p += li(BASE, DATA) + li(AMO_PTR, DATA + 0x200)
    for _ in range(n):
        k = rng.random()
        if k < 0.20:
            f3 = rng.randrange(8)
            f7 = 0x20 if f3 in (0, 5) and rng.random() < 0.5 else 0
            p.append(_r(f7, rs(), rs(), f3, rd()))
        elif k < 0.38:
            f3 = rng.randrange(8)
            if f3 == 1:   p.append(_r(0, rng.randrange(32), rs(), 1, rd(), 0x13))
            elif f3 == 5: p.append(_r(rng.choice([0, 0x20]), rng.randrange(32), rs(), 5, rd(), 0x13))
            else:         p.append(_i(0x13, rd(), f3, rs(), rng.getrandbits(12)))
        elif k < 0.52:
            f3 = rng.choice([0, 1, 2, 4, 5])
            align = {0: 1, 4: 1, 1: 2, 5: 2, 2: 4}[f3]
            p.append(_i(0x03, rd(), f3, BASE, rng.randrange(0, 0x400, align)))
        elif k < 0.64:
            f3 = rng.choice([0, 1, 2])
            align = {0: 1, 1: 2, 2: 4}[f3]
            p.append(_s(f3, BASE, rs(), rng.randrange(0, 0x400, align)))
        elif k < 0.68: p.append(lui(rd(), rng.getrandbits(20)))
        elif k < 0.72: p.append(auipc(rd(), rng.getrandbits(20)))
        elif k < 0.80:                                        # forward branch over 1 or 2
            p.append(_b(rng.choice([0, 1, 4, 5, 6, 7]), rs(), rs(), rng.choice([8, 12])))
        elif k < 0.83:
            p.append(_j(rd(), 8))                             # jal over one instruction
        elif k < 0.86:                                        # jalr over one, odd target
            # absolute (rs1 = x0): a branch may skip whatever precedes it
            p.append(jalr(rd(), 0, 4 * len(p) + rng.choice([8, 9])))
        elif k < 0.94:
            p.append(amoswap(rd(), rs(), AMO_PTR, rng.randrange(4)))
        elif k < 0.97:
            p.append(addi(AMO_PTR, BASE, 0x200 + 4 * rng.randrange(16)))
        elif unknown:
            p.append(rng.choice([0x0000_000F, 0x0000_0073, 0x0010_0073]) if rng.random() < 0.5
                     else (rng.getrandbits(25) << 7) | rng.choice([0x0B, 0x2B, 0x5B, 0x7B, 0x57, 0x77, 0x07, 0x27]))
        else:
            p.append(addi(0, 0, 0))
    p += [sw(r, BASE, DUMP - DATA + 4 * r) for r in range(1, 16)]
    p.append(HALT)
    assert len(p) * 4 <= DATA, len(p)
    return p


def directed_program():
    """Every instruction type once, with known operands."""
    p = []
    p += li(1, 0x8000_0001) + li(2, 0x0000_0003) + li(15, 0) + li(BASE, DATA) + li(AMO_PTR, DATA + 0x100)
    p += [_r(0, 2, 1, f3, 3 + f3 % 8) for f3 in range(8)]                 # add..and
    p += [_r(0x20, 2, 1, 0, 11), _r(0x20, 2, 1, 5, 12)]                   # sub, sra
    p += [_i(0x13, 4, f3, 1, 0xFF7) for f3 in (0, 2, 3, 4, 6, 7)]         # addi..andi
    p += [_r(0, 31, 1, 1, 5, 0x13), _r(0, 4, 1, 5, 6, 0x13), _r(0x20, 4, 1, 5, 7, 0x13)]
    p += [_s(f3, BASE, 1, o) for f3, o in ((0, 1), (1, 6), (2, 8))]       # sb sh sw
    p += [_i(0x03, 8, f3, BASE, o) for f3, o in ((0, 3), (1, 2), (2, 8), (4, 1), (5, 6))]
    p += [lui(9, 0xABCDE), auipc(10, 0x00012)]
    for f3 in (0, 1, 4, 5, 6, 7):                                         # every branch both ways
        p += [_b(f3, 1, 2, 8), addi(15, 15, 1), _b(f3, 2, 1, 8), addi(15, 15, 2)]
    p += [_j(11, 8), addi(15, 15, 64), auipc(12, 0), jalr(12, 12, 13), addi(15, 15, 128)]
    p += [amoswap(3, 1, AMO_PTR), amoswap(0, 2, AMO_PTR), amoswap(4, 4, AMO_PTR)]
    p += [sw(r, BASE, DUMP - DATA + 4 * r) for r in range(1, 16)] + [HALT]
    return p


# --------------------------------------------------------------------------
# bench
# --------------------------------------------------------------------------
class Bench:
    def __init__(self, dut, rng, i_stall=(0, 0, 1, 3), d_stall=(0, 0, 1, 2, 4)):
        self.d, self.rng = dut, rng
        self.i_stall, self.d_stall = i_stall, d_stall
        self.mem = [0] * WORDS
        self.retired, self.acc = [], []
        self.i_wait = self.d_wait = None
        self.pending = None
        self.i_hold = self.d_hold = None
        self.atomic_seen = []          # (d_atomic, is_amo_access) per granted access
        self.force_state = None             # an unused state encoding to force once
        self.forced = None
        self.recovered = []
        self.cycle = 0

    def load(self, prog):
        self.mem = [self.rng.getrandbits(32) if DATA <= 4 * i < DATA + 0x400 else 0 for i in range(WORDS)]
        for i, w in enumerate(prog):
            self.mem[i] = w
        return list(self.mem)

    async def reset(self):
        d = self.d
        d.rst.value = 1
        d.i_valid.value = 0; d.i_rdata.value = 0
        d.d_gnt.value = 0; d.d_rdata.value = 0
        for _ in range(3):
            await FallingEdge(d.clk)
        d.rst.value = 0
        self.retired, self.acc = [], []
        self.i_wait = self.d_wait = self.pending = None
        self.i_hold = self.d_hold = None

    async def step(self):
        d, rng = self.d, self.rng
        await FallingEdge(d.clk)
        self.cycle += 1
        state = int(d.state.value)
        if self.forced is not None:                 # the cycle after forcing
            assert state == 0, f"state {self.forced} did not return to S_FETCH"
            self.recovered.append(self.forced)
            self.forced = None
        if self.force_state is not None and state == 0 and len(self.retired) > 20:
            # force an unused encoding while in S_FETCH, driving nothing this cycle
            d.state.value = self.force_state
            d.i_valid.value = 0
            d.d_gnt.value = 0
            d.d_rdata.value = self.rng.getrandbits(32)
            self.i_wait = self.i_hold = None
            await Timer(1, "ns")
            assert int(d.state.value) == self.force_state
            assert int(d.i_req.value) == 0 and int(d.d_req.value) == 0 and int(d.retire.value) == 0, \
                "requests or a retire from an illegal state"
            self.forced, self.force_state = self.force_state, None
            return state
        # instruction port
        i_req = int(d.i_req.value)
        d.i_valid.value = 0
        d.i_rdata.value = rng.getrandbits(32)
        if i_req:
            ia = int(d.i_addr.value)
            if self.i_hold is not None:
                assert ia == self.i_hold, "i_addr changed while i_req was waiting"
            self.i_hold = ia
            if self.i_wait is None:
                self.i_wait = rng.choice(self.i_stall)
            if self.i_wait == 0:
                d.i_valid.value = 1
                d.i_rdata.value = self.mem[ia >> 2]
                self.i_wait = self.i_hold = None
            else:
                self.i_wait -= 1
        # data port: read data for last cycle's read grant, garbage otherwise
        d.d_rdata.value = self.pending if self.pending is not None else rng.getrandbits(32)
        self.pending = None
        d.d_gnt.value = 0
        if int(d.d_req.value):
            bundle = (int(d.d_addr.value), int(d.d_wdata.value), int(d.d_wstrb.value), int(d.d_atomic.value))
            if self.d_hold is not None:
                assert bundle == self.d_hold, "data bundle changed while d_req was waiting"
            self.d_hold = bundle
            if self.d_wait is None:
                self.d_wait = rng.choice(self.d_stall)
            if self.d_wait == 0:
                d.d_gnt.value = 1
                addr, wdata, wstrb, atomic = bundle
                idx = addr >> 2
                assert addr & 3 == 0, "d_addr not word aligned"
                if wstrb == 0:
                    self.acc.append(("R", idx))
                    self.pending = self.mem[idx]
                else:
                    mask = sum(0xFF << (8 * k) for k in range(4) if wstrb >> k & 1)
                    self.acc.append(("W", idx, wstrb, wdata & mask))
                    self.mem[idx] = (self.mem[idx] & ~mask & M) | (wdata & mask)
                self.atomic_seen.append((atomic, state == 4))
                self.d_wait = self.d_hold = None
            else:
                self.d_wait -= 1
        else:
            assert int(d.d_atomic.value) == 0 or state == 4
        await Timer(1, "ns")
        if int(d.retire.value):
            self.retired.append(int(d.retire_pc.value))
        return state

    async def run_program(self, prog, amo_off, max_cycles=60000):
        """Load, reset, run to HALT; compare with the ISS."""
        exp_pcs, exp_acc, exp_mem = iss(self.load(prog), amo_off)
        self.d.amo_off.value = amo_off
        await self.reset()
        halt_pc = exp_pcs[-1]
        for _ in range(max_cycles):
            await self.step()
            if self.retired and self.retired[-1] == halt_pc and len(self.retired) >= len(exp_pcs):
                break
        else:
            raise AssertionError(f"core did not reach HALT in {max_cycles} cycles "
                                 f"({len(self.retired)} of {len(exp_pcs)} retired)")
        n = len(exp_pcs)
        for k, (g, e) in enumerate(zip(self.retired[:n], exp_pcs)):
            assert g == e, f"retire #{k}: pc {g:#x}, ISS {e:#x} (instruction {self.mem[e >> 2]:#010x})"
        for k, (g, e) in enumerate(zip(self.acc, exp_acc)):
            assert g == e, f"data access #{k}: core {g}, ISS {e}"
        assert len(self.acc) == len(exp_acc), f"{len(self.acc)} data accesses, ISS {len(exp_acc)}"
        assert self.mem == exp_mem, "final memory differs from the ISS"
        return len(exp_pcs)


async def new_bench(dut, seed_offset=0, **kw):
    seed = int(os.environ.get("SEED", "1")) + seed_offset
    dut._log.info(f"SEED={seed} NREGS={NREGS}")
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.amo_off.value = 0
    return Bench(dut, random.Random(seed), **kw)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
@cocotb.test()
async def directed_isa(dut):
    tb = await new_bench(dut, i_stall=(0,), d_stall=(0,))
    await tb.run_program(directed_program(), amo_off=0)


@cocotb.test()
async def stalls_every_state(dut):
    """Long stalls on both ports: every waiting state is held for several cycles."""
    tb = await new_bench(dut, 1, i_stall=(3, 5), d_stall=(2, 4, 6))
    await tb.run_program(directed_program(), amo_off=0)
    await tb.run_program(directed_program(), amo_off=1)


@cocotb.test()
async def random_programs(dut):
    tb = await new_bench(dut, 2)
    total = 0
    for k in range(int(os.environ.get("PROGS", "25"))):
        total += await tb.run_program(gen_program(tb.rng), amo_off=0)
    dut._log.info(f"{total} instructions retired and matched")


@cocotb.test()
async def random_amo_off(dut):
    """AMO-1: with amo_off = 1 amoswap retires as a no-op."""
    tb = await new_bench(dut, 3)
    for k in range(int(os.environ.get("PROGS", "25")) // 2):
        await tb.run_program(gen_program(tb.rng), amo_off=1)
    assert all(not a for a, _ in tb.atomic_seen), "d_atomic high with amo_off = 1"
    assert not any(amo for _, amo in tb.atomic_seen), "an access was made from S_AMO_WAIT with amo_off = 1"


@cocotb.test()
async def amo_d_atomic(dut):
    """d_atomic is high for exactly the accesses made from S_AMO_WAIT."""
    tb = await new_bench(dut, 4)
    for _ in range(5):
        await tb.run_program(gen_program(tb.rng), amo_off=0)
    assert any(a for a, _ in tb.atomic_seen), "no atomic accesses seen"
    for atomic, amo in tb.atomic_seen:
        assert atomic == amo, "d_atomic does not match an amoswap access"


@cocotb.test()
async def unknown_opcodes(dut):
    """fence, ecall, ebreak and undefined opcodes retire as no-ops."""
    tb = await new_bench(dut, 5)
    for _ in range(5):
        await tb.run_program(gen_program(tb.rng, unknown=True), amo_off=0)


@cocotb.test()
async def reset_mid_program(dut):
    """rst at a random point: the core restarts from pc 0 and the rerun matches."""
    tb = await new_bench(dut, 6)
    for _ in range(4):
        prog = gen_program(tb.rng, n=80)
        image = tb.load(prog)
        await tb.reset()
        for _ in range(tb.rng.randrange(20, 300)):
            await tb.step()
        # reset with requests possibly outstanding, then rerun from a fresh image
        tb.mem = list(image)
        await tb.reset()
        assert int(dut.state.value) == 0 and int(dut.d_req.value) == 0
        assert int(dut.i_addr.value) == 0, "pc not 0 after reset"
        await tb.run_program(prog, amo_off=0)


@cocotb.test()
async def illegal_state_recovers(dut):
    """Force the 3-bit state register to each unused encoding (5, 6, 7) while
    in S_FETCH: no request or retire comes from it, the default arm returns it
    to S_FETCH the next cycle, and the program still matches the ISS."""
    tb = await new_bench(dut, 7, i_stall=(0, 1), d_stall=(0, 1))
    for bad in (5, 6, 7):
        tb.force_state = bad
        await tb.run_program(gen_program(tb.rng, n=60), amo_off=0)
    assert tb.recovered == [5, 6, 7], f"forced and recovered: {tb.recovered}"


def test_core_unit():
    """pytest entry point: NREGS = 16 (the design) and 32."""
    srcs = ["rtl/core/" + f for f in ("decoder.sv", "imm_gen.sv", "register_file.sv",
                                      "alu.sv", "mem_access.sv", "amoswap.sv", "core.sv")]
    for n in (16, 32):
        os.environ["CORE_NREGS"] = str(n)
        run("core", srcs, "core", "test_core_unit", parameters={"NREGS": n}, tag=f"_{n}")
