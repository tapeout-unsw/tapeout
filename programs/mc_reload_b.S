# Loaded over mc_reload_a without a reset. Same addresses, different code, so
# a stale I-cache would print A again. Checks LOCK and EXIT were cleared.
# Failure codes: L lock held, E exit set.

.include "common.inc"

.section .text
.globl _start
_start:
    nop                         # shift everything: no instruction lines up
    nop                         # with mc_reload_a at the same address
    PROLOGUE
    lw   t0, LOCK_PEEK(s0)
    beqz t0, 1f
    FAIL 'L'
1:  lw   t0, EXIT(s0)
    beqz t0, 2f
    FAIL 'E'
2:  BARRIER mid_flags           # nobody exits before both checked
    ACQUIRE
    RELEASE
    FINISH

.section .data
.balign 4
errors:    .word 0
mid_flags: .word 0, 0
end_flags: .word 0, 0
