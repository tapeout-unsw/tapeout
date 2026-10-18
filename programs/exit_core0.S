# Core 0 exits first; core 1 waits for its EXIT flag before exiting.
.section .text
.globl _start
_start:
    lui s0, 1
    lw s1, 12(s0)
    bnez s1, wait_exit
    sw zero, 16(s0)
halt:
    j halt
wait_exit:
    lw a0, 16(s0)
    andi t0, a0, 1
    beqz t0, wait_exit
    sw zero, 16(s0)
park:
    j park
