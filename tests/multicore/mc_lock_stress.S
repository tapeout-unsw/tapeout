# 100 critical sections per core around a slow increment of a shared counter.
# Core 0 uses lw/sw on LOCK, core 1 lbu/sb. Counter must end at 200.
# Failure codes: O two owners, C wrong count.

.include "common.inc"
.equ N, 100

.section .text
.globl _start
_start:
    PROLOGUE
    la   a2, counter
    la   a3, owner
    addi a4, s1, 1              # owner tag: 1 or 2
    li   a5, N
loop:
    beqz s1, acq_word
acq_byte:
    lbu  t2, LOCK(s0)
    bnez t2, acq_byte
    j    inside
acq_word:
    lw   t2, LOCK(s0)
    bnez t2, acq_word
inside:
    lw   t0, 0(a3)              # nobody else may be inside
    beqz t0, 1f
    FAIL 'O'
1:  sw   a4, 0(a3)
    lw   t0, 0(a2)
    li   t1, 5                  # widen the race window
2:  addi t1, t1, -1
    bnez t1, 2b
    addi t0, t0, 1
    sw   t0, 0(a2)
    lw   t0, 0(a3)
    beq  t0, a4, 3f
    FAIL 'O'
3:  sw   zero, 0(a3)
    beqz s1, rel_word
    sb   zero, LOCK(s0)
    j    next
rel_word:
    sw   zero, LOCK(s0)
next:
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
counter:   .word 0
owner:     .word 0
errors:    .word 0
mid_flags: .word 0, 0
end_flags: .word 0, 0
