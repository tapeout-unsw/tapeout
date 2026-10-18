# Read the core ID, set a different x5 on each core, then write EXIT.
.section .text
.globl _start
_start:
    li x5, 0x100c
    lw x5, 0(x5)
    andi x5, x5, 1
    beq x5, x0, core0
core1:
    li x5, 0xff00
    j finish
core0:
    li x5, 0x00ff
finish:
    lui t1, 1
    sw zero, 16(t1)
halt:
    j halt
