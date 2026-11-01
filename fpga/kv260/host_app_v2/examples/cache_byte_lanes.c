#include "tapeout.h"

// Warm a data-cache line, overwrite individual bytes, then reload the word.
// Assumes the processor's little-endian memory layout.
// Expected: "byte lanes: PASS" with data caches enabled and bypassed.
volatile unsigned int word = 0x11223344u;

int main(void) {
    if (tapeout_core_id() != 0) return 0;
    volatile unsigned char *bytes = (volatile unsigned char *)&word;
    volatile unsigned int warmed = word;
    (void)warmed;
    bytes[0] = 0xaau;
    bytes[2] = 0xbbu;
    if (word == 0x11bb33aau) tapeout_puts("byte lanes: PASS\n");
    else tapeout_puts("byte lanes: FAIL\n");
    return 0;
}
