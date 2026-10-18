# Two cores increment a shared counter under the MMIO lock
# The initialized data travels in the binary loaded onto the CPU
.section .text
.globl _start
_start:
    lui s0, 1
    lw s1, 12(s0)
    la a2, counter
    li a3, 32
acquire:
    lw t0, 8(s0)
    bnez t0, acquire
    lw t1, 0(a2)
    addi t1, t1, 1
    sw t1, 0(a2)
    sw zero, 8(s0)
    addi a3, a3, -1
    bnez a3, acquire

    # Publish completion and wait for the peer before reading the total into a0
    slli t0, s1, 2
    add t0, a2, t0
    li t1, 1
    sw t1, 4(t0)
    xori t0, s1, 1
    slli t0, t0, 2
    add t0, a2, t0
wait_peer:
    lw t1, 4(t0)
    beqz t1, wait_peer
    lw a0, 0(a2)
    lui t2, 1
    sw zero, 16(t2)
halt:
    j halt
.section .data
.balign 4
counter:
    .word 0
finished:
    .word 0, 0
