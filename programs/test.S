.equ CORE_ADDR, 0x100C

_start:
    lui x5, 0x1
    lw x5, 0xC(x5)

    andi x5, x5, 1

    beq x5, x0, core0

core1:
    lui x5, 0xff00
    jal x0, core1

core0:
    lui x5, 0x00ff
    jal x0, core0
