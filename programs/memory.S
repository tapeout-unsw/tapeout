# Use separate words for each core and leave load results in registers
.section .text
.globl _start
_start:
    lui t0, 1
    lw s0, 12(t0)
    slli s0, s0, 2
    la s1, words
    add s1, s1, s0
    li t0, 0x12345678
    sw t0, 0(s1)
    lw a0, 0(s1)

    li a2, 0
    li a3, 4
bytes:
    add a4, s1, a2
    li t0, -128
    sb t0, 0(a4)
    lb a0, 0(a4)
    lbu a1, 0(a4)
    addi a2, a2, 1
    blt a2, a3, bytes

    li t0, 0x8001
    sh t0, 0(s1)
    lh a2, 0(s1)
    lhu a3, 0(s1)
    li t0, 0x8002
    sh t0, 2(s1)
    lh a4, 2(s1)
    lhu a5, 2(s1)
    lw t1, 0(s1)
    lui t2, 1
    sw zero, 16(t2)
halt:
    j halt
.section .data
.balign 4
words:
    .word 0, 0
