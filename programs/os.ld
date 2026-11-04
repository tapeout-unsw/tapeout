ENTRY(_start)
SECTIONS
{
    . = 0;
    .text : { *(.text*) }
    .data : { *(.data*) *(.rodata*) }
    .bss : { *(.bss*) }
    ASSERT(. <= 0x300, "OS overlaps core 1 payload at 0x300")
}
