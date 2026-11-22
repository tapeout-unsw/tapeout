# Both cores enter main. Core 0 checks 37 + 12 and prints P (pass) or F (fail)
# through the processor UART. Core 1 returns immediately. The host app's
# runtime handles startup and makes each core write its EXIT flag on return.

.section .text
.globl main
.type main, @function
main:
    lui  t0, 1             # t0 = MMIO base 0x1000
    lw   t1, 12(t0)        # Read CORE_ID at 0x100c.
    bnez t1, done          # Only core 0 prints.

    addi t1, zero, 37      # First input.
    addi t2, zero, 12      # Second input.
    add  t1, t1, t2        # Add the inputs.
    addi t2, zero, 49      # Expected result.
    bne  t1, t2, fail
    addi t1, zero, 80      # ASCII 'P'.
    j    send
fail:
    addi t1, zero, 70      # ASCII 'F'.
send:
    lw   t2, 4(t0)         # Poll UART_STATUS until the transmitter is ready.
    andi t2, t2, 1
    bnez t2, send
    sw   t1, 0(t0)         # Write the character to UART_DATA.
done:
    addi a0, zero, 0       # Return success to the runtime.
    ret
.size main, .-main
