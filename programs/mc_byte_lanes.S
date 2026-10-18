# Both cores store bytes and halfwords into the same words at once, no lock.
# Checks readback, the final words, and lb/lh sign extension.
# Failure codes: b B s (bytes), h H x (halfwords).

.include "common.inc"
.equ WORDS, 16
.equ BROUNDS, 20
.equ HROUNDS, 10

.section .text
.globl _start
_start:
    PROLOGUE
    li   a5, 0x80
    bnez s1, 1f
    li   a5, 0
1:
    li   a3, 0                  # round
b_round:
    la   a2, bytes
    li   a4, 0                  # word index
b_word:
    add  t0, a3, a4
    add  t0, t0, a5
    andi t0, t0, 0xff           # value = (round + i + 0x80*id) & 0xff
    add  t1, a2, s1             # byte address of lane id
    sb   t0, 0(t1)
    sb   t0, 2(t1)
    lbu  t2, 0(t1)
    bne  t2, t0, b_bad
    lbu  t2, 2(t1)
    beq  t2, t0, b_ok
b_bad:
    FAIL 'b'
b_ok:
    addi a2, a2, 4
    addi a4, a4, 1
    li   t2, WORDS
    blt  a4, t2, b_word
    addi a3, a3, 1
    li   t2, BROUNDS
    blt  a3, t2, b_round

    BARRIER flags1

    # Every word must be {v1, v0, v1, v0} from the last round.
    la   a2, bytes
    li   a4, 0
b_check:
    addi t0, a4, BROUNDS-1
    andi t0, t0, 0xff           # v0
    addi t1, t0, 0x80
    andi t1, t1, 0xff           # v1
    slli t2, t1, 8
    or   t2, t2, t0             # 16-bit {v1, v0}
    slli t0, t2, 16
    or   t0, t0, t2             # expected word
    lw   t2, 0(a2)
    beq  t2, t0, 1f
    FAIL 'B'
1:  lb   t2, 1(a2)              # core 1's byte has bit 7 set: must sign-extend
    slli t0, t1, 24
    srai t0, t0, 24
    beq  t2, t0, 2f
    FAIL 's'
2:  addi a2, a2, 4
    addi a4, a4, 1
    li   t2, WORDS
    blt  a4, t2, b_check

    slli a5, s1, 12             # 0x0000 or 0x1000
    li   t0, 0x8000
    or   a5, a5, t0             # bit 15 set: lh must sign-extend
    li   a3, 0
h_round:
    la   a2, halves
    slli t0, s1, 1
    add  a2, a2, t0             # halfword offset 2*id
    li   a4, 0
h_word:
    add  t0, a3, a4
    or   t0, t0, a5             # value = 0x8000 | id<<12 | (round + i)
    sh   t0, 0(a2)
    lhu  t2, 0(a2)
    beq  t2, t0, 1f
    FAIL 'h'
1:  lh   t2, 0(a2)
    lui  t1, 0xFFFF0
    or   t1, t1, t0
    beq  t2, t1, 2f
    FAIL 'x'
2:  addi a2, a2, 4
    addi a4, a4, 1
    li   t2, WORDS
    blt  a4, t2, h_word
    addi a3, a3, 1
    li   t2, HROUNDS
    blt  a3, t2, h_round

    BARRIER flags2

    la   a2, halves
    li   a4, 0
h_check:
    addi t0, a4, HROUNDS-1
    li   t1, 0x8000
    or   t0, t0, t1             # core 0's half
    li   t1, 0x1000
    or   t1, t0, t1             # core 1's half
    slli t1, t1, 16
    or   t0, t0, t1
    lw   t2, 0(a2)
    beq  t2, t0, 1f
    FAIL 'H'
1:  addi a2, a2, 4
    addi a4, a4, 1
    li   t2, WORDS
    blt  a4, t2, h_check

    FINISH

.section .data
.balign 4
bytes:     .fill 16, 4, 0
halves:    .fill 16, 4, 0
errors:    .word 0
flags1:    .word 0, 0
flags2:    .word 0, 0
end_flags: .word 0, 0
