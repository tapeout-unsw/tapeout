# Each core fills half of 0x400-0x7FF, then the other core checks it with word
# and byte loads, rewrites it with the complement and checks again.
# Failure codes: W word, Y byte, Z after rewrite.

.include "common.inc"
.equ BASE, 0x400
.equ HALF, 0x200

.section .text
.globl _start
_start:
    PROLOGUE
    lui  a5, 0xA5A50            # pattern = 0xA5A50000 ^ addr ^ (writer << 12)
    slli a2, s1, 9
    addi a2, a2, BASE           # start of own half
    li   a3, HALF/4
    slli a4, s1, 12
fill:
    xor  t0, a2, a5
    xor  t0, t0, a4
    sw   t0, 0(a2)
    addi a2, a2, 4
    addi a3, a3, -1
    bnez a3, fill

    BARRIER flags1

    xori t0, s1, 1
    slli a2, t0, 9
    addi a2, a2, BASE
    slli a4, t0, 12             # the other core wrote it
    li   a3, HALF/4
check_w:
    xor  t0, a2, a5
    xor  t0, t0, a4
    lw   t1, 0(a2)
    beq  t0, t1, 1f
    FAIL 'W'
1:  lbu  t1, 3(a2)              # top byte via a byte load
    srli t0, t0, 24
    beq  t0, t1, 2f
    FAIL 'Y'
2:  addi a2, a2, 4
    addi a3, a3, -1
    bnez a3, check_w

    xori t0, s1, 1
    slli a2, t0, 9
    addi a2, a2, BASE
    li   a3, HALF/4
rewrite:
    lw   t0, 0(a2)
    not  t0, t0
    sw   t0, 0(a2)
    lw   t1, 0(a2)
    beq  t0, t1, 3f
    FAIL 'Z'
3:  addi a2, a2, 4
    addi a3, a3, -1
    bnez a3, rewrite

    FINISH

.section .data
.balign 4
errors:    .word 0
flags1:    .word 0, 0
end_flags: .word 0, 0
