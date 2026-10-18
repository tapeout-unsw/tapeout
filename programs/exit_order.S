# Core 1 exits first; core 0 waits for its EXIT flag before exiting.
.section .text
.globl _start
_start:
    lui s0, 1
    lw s1, 12(s0)
    li t0, 1
    bne s1, t0, wait_exit
    sw zero, 16(s0)
halt:
    j halt
wait_exit:
    lw a0, 16(s0)
    andi t0, a0, 2
    beqz t0, wait_exit
    sw zero, 16(s0)
park:
    j park
