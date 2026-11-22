# amoswap locks with the D-cache on. Both cores take a lock built on amoswap.w
# around a slow increment of a shared counter, 100 times each: core 0 uses
# test-and-test-and-set (spin on lw, then swap), core 1 plain test-and-set
# (spin on the swap). Counter must end at 200.
# With DCACHE=1 the lock word is cached (core 0 spins on it with lw, and both
# cores read it back once they own it), so this checks that amoswap bypasses
# the D-cache and leaves no stale copy behind.
# Failure codes: O two owners, L lock word reads 0 right after we took it,
# C wrong count.

.include "common.inc"
.equ N, 100

# amoswap.w rd, rs2, (rs1). Written with .insn because the programs are
# assembled with -march=rv32e, which has no A extension.
.macro AMOSWAP rd, rs2, rs1
    .insn r 0x2F, 2, 0x04, \rd, \rs1, \rs2
.endm

.section .text
.globl _start
_start:
    PROLOGUE
    la   a1, lock
    la   a2, counter
    la   a3, owner
    addi a4, s1, 1              # owner tag: 1 or 2
    li   a5, N
loop:
    bnez s1, tas
ttas:                           # core 0: spin on a (cacheable) load, then swap
    lw   t2, 0(a1)
    bnez t2, ttas
    AMOSWAP t2, a4, a1
    bnez t2, ttas
    j    inside
tas:                            # core 1: spin on the swap itself
    AMOSWAP t2, a4, a1
    bnez t2, tas
inside:
    lw   t0, 0(a1)              # the lock must read as taken (not a stale 0).
    bnez t0, 1f                 # Not "our tag": core 1's failed swaps write 2
    FAIL 'L'                    # into it while core 0 holds it.
1:  lw   t0, 0(a3)              # nobody else may be inside
    beqz t0, 2f
    FAIL 'O'
2:  sw   a4, 0(a3)
    lw   t0, 0(a2)
    li   t1, 5                  # widen the race window
3:  addi t1, t1, -1
    bnez t1, 3b
    addi t0, t0, 1
    sw   t0, 0(a2)
    lw   t0, 0(a3)
    beq  t0, a4, 4f
    FAIL 'O'
4:  sw   zero, 0(a3)
    sw   zero, 0(a1)            # release
    addi a5, a5, -1
    bnez a5, loop

    BARRIER mid_flags
    lw   t0, 0(a2)
    li   t1, 2*N
    beq  t0, t1, done
    FAIL 'C'
done:
    FINISH

.section .data
.balign 4
lock:      .word 0
counter:   .word 0
owner:     .word 0
errors:    .word 0
mid_flags: .word 0, 0
end_flags: .word 0, 0
