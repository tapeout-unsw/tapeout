# First half of the reload test. Core 0 takes LOCK and never releases it,
# both cores write EXIT, core 0 prints A.

.include "common.inc"

.section .text
.globl _start
_start:
    PROLOGUE
    bnez s1, 1f
    ACQUIRE                     # core 0 holds the lock, deliberately
1:  li   a5, 20                 # loop so both caches hold this code
2:  addi a5, a5, -1
    bnez a5, 2b
    BARRIER end_flags
    bnez s1, 3f
    PUTI 'A'
3:  sw   zero, EXIT(s0)
4:  j    4b

.section .data
.balign 4
end_flags: .word 0, 0
