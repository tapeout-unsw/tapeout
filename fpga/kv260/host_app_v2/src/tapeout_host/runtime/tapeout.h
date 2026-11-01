#ifndef TAPEOUT_H
#define TAPEOUT_H

#include <stdint.h>

// Both RV32E cores enter main(). Use the core ID when they need separate work.
uint32_t tapeout_core_id(void);

// UART output is memory mapped at 0x1000. A program should normally let only
// one core print, otherwise messages from the two cores can interleave.
void tapeout_putchar(char value);
void tapeout_puts(const char *text);

// The lock read is an atomic test-and-set performed by the RTL MMIO block.
void tapeout_lock(void);
void tapeout_unlock(void);

// Mark the calling core complete. DONE rises once both cores have exited.
void tapeout_exit(void) __attribute__((noreturn));

#endif
