ENTRY(_start)

MEMORY
{
    RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 0x400
}

SECTIONS
{
    .text : {
        KEEP(*(.text.start))
        *(.text*)
    } > RAM

    .rodata : {
        *(.rodata*)
    } > RAM

    .data : {
        *(.startup)
        *(.sdata*)
        *(.data*)
    } > RAM

    .bss (NOLOAD) : ALIGN(4) {
        __bss_start = .;
        *(.sbss*)
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM

    /* The last 128 bytes are two private 64-byte stacks. */
    __stack1_top = 0x000003c0;
    __stack0_top = 0x00000400;
    ASSERT(__bss_end <= 0x00000380, "program/data exceed 896-byte RV32E region")
}
