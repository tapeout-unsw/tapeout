ENTRY(_start)

MEMORY
{
    RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 0x800
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
    __stack1_top = 0x000007c0;
    __stack0_top = 0x00000800;
    ASSERT(__bss_end <= 0x00000780, "program/data exceed 1920-byte RV32E region")
}

