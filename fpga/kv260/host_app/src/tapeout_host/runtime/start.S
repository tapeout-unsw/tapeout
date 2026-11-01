.section .text.start
.globl _start
.type _start, @function

// Both cores start at address zero. They receive separate 64-byte stacks at
// the top of SRAM. Core 0 initializes BSS, then releases core 1 into main().
_start:
    li      t0, 0x100c
    lw      s0, 0(t0)
    andi    s0, s0, 1
    beqz    s0, .Lcore0

.Lcore1:
    la      sp, __stack1_top
    la      t0, __startup_ready
.Lwait_for_bss:
    lw      t1, 0(t0)
    beqz    t1, .Lwait_for_bss
    j       .Lmain

.Lcore0:
    la      sp, __stack0_top
    la      t0, __bss_start
    la      t1, __bss_end
.Lclear_bss:
    bgeu    t0, t1, .Lbss_done
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       .Lclear_bss
.Lbss_done:
    la      t0, __startup_ready
    li      t1, 1
    sw      t1, 0(t0)

.Lmain:
    call    main
    call    tapeout_exit
.Lpark:
    j       .Lpark
.size _start, .-_start
