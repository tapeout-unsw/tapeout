ENTRY(_start)

SECTIONS
{
    . = 0x00000000;

    .text : {
        *(.text*)
    }
    .data : {
        *(.data*)
        *(.rodata*)
    }
    .bss : {
        *(.bss*)
    }
    ASSERT(. <= 0x400, "Program exceeds 1 KiB RAM")
}
