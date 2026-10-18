# Five functions that alias in the I-cache, called in turn on both cores, so
# lines are evicted on every call. A wrong instruction gives a wrong sum.
# Failure code: S.

.include "common.inc"
.equ ITERS, 40

.section .text
.globl _start
_start:
    PROLOGUE
    li   a0, 0
    li   a5, ITERS
loop:
    jal  ra, f0
    jal  ra, f1
    jal  ra, f2
    jal  ra, f3
    la   t0, f4
    jalr ra, 0(t0)
    addi a5, a5, -1
    bnez a5, loop

    li   t0, ITERS * (1 + 20 + 300 + 4000 + 50000)
    beq  a0, t0, ok
    FAIL 'S'
ok:
    FINISH

# Each function is 8 words, so every one of them covers sets 0 to 7.
.balign 32
f0: addi a0, a0, 1
    nop
    nop
    nop
    nop
    nop
    nop
    ret
.balign 32
f1: addi a0, a0, 20
    nop
    nop
    nop
    nop
    nop
    nop
    ret
.balign 32
f2: addi a0, a0, 300
    nop
    nop
    nop
    nop
    nop
    nop
    ret
.balign 32
f3: li   t1, 4000
    add  a0, a0, t1
    nop
    nop
    nop
    nop
    nop
    ret
.balign 32
f4: li   t1, 10000              # 20 straight-line words
    add  a0, a0, t1
    add  a0, a0, t1
    add  a0, a0, t1
    add  a0, a0, t1
    add  a0, a0, t1
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    nop
    ret

.section .data
.balign 4
errors:    .word 0
end_flags: .word 0, 0
