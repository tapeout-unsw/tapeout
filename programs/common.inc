# s0 = MMIO base, s1 = CORE_ID. FINISH: core 0 prints P if errors is zero,
# else F and the code, then both cores write EXIT.
# Macros use local labels 91-96, so tests must not.

.equ UART_DATA,   0x00
.equ UART_STATUS, 0x04
.equ LOCK,        0x08
.equ CORE_ID,     0x0C
.equ EXIT,        0x10
.equ LOCK_PEEK,   0x14

# Every test starts here: s0 = MMIO base, s1 = CORE_ID.
.macro PROLOGUE
    lui  s0, 1
    lw   s1, CORE_ID(s0)
.endm

# Send the low byte of \r once the transmitter is idle. Clobbers t2.
.macro PUTC r
91:  lw   t2, UART_STATUS(s0)
    andi t2, t2, 1
    bnez t2, 91b
    sw   \r, UART_DATA(s0)
.endm

# Send an immediate character. Clobbers t1, t2.
.macro PUTI c
    li   t1, \c
    PUTC t1
.endm

# Spin on the test-and-set LOCK register. Clobbers t2.
.macro ACQUIRE
92:  lw   t2, LOCK(s0)
    bnez t2, 92b
.endm

.macro RELEASE
    sw   zero, LOCK(s0)
.endm

# Wait for the other core. \flags is two zeroed words, one pair per barrier.
# Clobbers t0, t1, t2.
.macro BARRIER flags
    la   t0, \flags
    slli t1, s1, 2
    add  t1, t0, t1
    li   t2, 1
    sw   t2, 0(t1)
    xori t1, s1, 1
    slli t1, t1, 2
    add  t1, t0, t1
93:  lw   t2, 0(t1)
    beqz t2, 93b
.endm

# Store a non-zero failure code in errors. Clobbers t1, t2.
.macro FAIL code
    la   t1, errors
    li   t2, \code
    sw   t2, 0(t1)
.endm

# Common ending. Needs the data words errors and end_flags (2 words).
.macro FINISH
    BARRIER end_flags
    bnez s1, 96f
    la   t0, errors
    lw   a0, 0(t0)
    bnez a0, 94f
    PUTI 'P'
    j    96f
94:  PUTI 'F'
    PUTC a0
96:  sw   zero, EXIT(s0)
95:  j    95b
.endm
