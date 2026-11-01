#include "tapeout.h"

#define MMIO32(address) (*(volatile uint32_t *)(address))

#define UART_DATA   0x1000u
#define UART_STATUS 0x1004u
#define LOCK        0x1008u
#define CORE_ID     0x100cu
#define EXIT        0x1010u

// This value is stored in the uploaded image, not BSS. Every reload therefore
// writes zero before either core starts. Core 1 waits while core 0 clears BSS.
__attribute__((section(".startup"))) volatile uint32_t __startup_ready = 0;

uint32_t tapeout_core_id(void) {
    return MMIO32(CORE_ID) & 1u;
}

void tapeout_putchar(char value) {
    while (MMIO32(UART_STATUS) & 1u) {
    }
    MMIO32(UART_DATA) = (uint8_t)value;
}

void tapeout_puts(const char *text) {
    while (*text != '\0') {
        tapeout_putchar(*text++);
    }
}

void tapeout_lock(void) {
    while (MMIO32(LOCK) & 1u) {
    }
}

void tapeout_unlock(void) {
    MMIO32(LOCK) = 0;
}

void tapeout_exit(void) {
    MMIO32(EXIT) = 0;
    for (;;) {
    }
}
