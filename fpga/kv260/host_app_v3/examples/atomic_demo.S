# Upload with Compile source. Core 0 attempts AMOSWAP on a private RAM word.
# UART prints E when atomic is enabled, D when disabled, F for an unexpected
# result. Both cores return to the runtime, which sets their EXIT bits.
.section .text
.globl main
.type main, @function
main:
    lui   t0, 1
    lw    t1, 12(t0)         # Core ID: only core 0 should print.
    bnez  t1, done
    la    t2, word
    li    a0, 42
    li    a1, 99             # Sentinel: AMOSWAP must preserve this if off.
    amoswap.w a1, a0, (t2)
    lw    a2, 0(t2)
    li    a3, 7
    bne   a1, a3, maybe_off
    li    a3, 42
    bne   a2, a3, bad
    li    t1, 69             # E: swap returned 7 and wrote 42.
    j     send
maybe_off:
    li    a3, 99
    bne   a1, a3, bad
    li    a3, 7
    bne   a2, a3, bad
    li    t1, 68             # D: register and memory unchanged.
    j     send
bad:
    li    t1, 70             # F: inconsistent result.
send:
    lw    t2, 4(t0)
    andi  t2, t2, 1
    bnez  t2, send
    sw    t1, 0(t0)
done:
    li    a0, 0
    ret
.size main, .-main
.section .data
.balign 4
word: .word 7
