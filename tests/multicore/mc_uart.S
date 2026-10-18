# Core 0 prints HELLO, then core 1 prints WORLD while hammering MMIO reads.
# Failure codes: I bad CORE_ID, R bad MMIO read.

.include "common.inc"

.section .text
.globl _start
_start:
    PROLOGUE
    la   t0, ids                # record CORE_ID + 1 in ids[CORE_ID]
    slli t1, s1, 2
    add  t0, t0, t1
    addi t1, s1, 1
    sw   t1, 0(t0)
    li   t0, 2
    bltu s1, t0, 1f             # CORE_ID must be 0 or 1
    FAIL 'I'
1:  bnez s1, core1

core0:
    PUTI 'H'
    PUTI 'E'
    PUTI 'L'
    PUTI 'L'
    PUTI 'O'
    la   t0, turn
    li   t1, 1
    sw   t1, 0(t0)
    j    finish

core1:
    la   a2, turn
wait_turn:                      # busy MMIO reads while core 0 prints
    lw   t0, CORE_ID(s0)
    li   t1, 1
    beq  t0, t1, 1f
    FAIL 'R'
1:  lw   t0, LOCK_PEEK(s0)
    beqz t0, 2f
    FAIL 'R'
2:  lw   t0, EXIT(s0)
    beqz t0, 3f
    FAIL 'R'
3:  lw   t0, 0(a2)
    beqz t0, wait_turn
    PUTI 'W'
    PUTI 'O'
    PUTI 'R'
    PUTI 'L'
    PUTI 'D'

finish:
    BARRIER mid_flags
    la   t0, ids                # both cores registered distinct ids
    lw   t1, 0(t0)
    lw   t2, 4(t0)
    li   t0, 1
    beq  t1, t0, 4f
    FAIL 'I'
4:  li   t0, 2
    beq  t2, t0, 5f
    FAIL 'I'
5:
    FINISH

.section .data
.balign 4
turn:      .word 0
ids:       .word 0, 0
errors:    .word 0
mid_flags: .word 0, 0
end_flags: .word 0, 0
