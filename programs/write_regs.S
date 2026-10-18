# Read the core ID and keep a different value in x5 on each core.
.section .text
.globl _start
_start:
    li x5, 0x100c
    lw x5, 0(x5)
    andi x5, x5, 1
    beq x5, x0, core0
core1:
    li x5, 0xff00
    jal x0, core1
core0:
    li x5, 0x00ff
    jal x0, core0
