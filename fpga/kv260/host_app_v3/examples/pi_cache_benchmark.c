#include "tapeout.h"

/* Machin's formula: pi = 16 atan(1/5) - 4 atan(1/239).
 * These are the first six and two terms respectively, scaled by 1 billion.
 * Both cores read the eight terms repeatedly, so they share the memory bus.
 * The values are volatile to force actual loads rather than constant folding.
 * This is a cache benchmark using a precomputed series, not a general pi library.
 * Expected output: pi ~ 3.141592652 followed by PASS.
 */
static const volatile unsigned int terms[8] = {
    3200000000u, 42666667u, 1024000u, 29257u,
    910u, 30u, 16736402u, 98u
};

#define ROUNDS 1000000u

int main(void) {
    unsigned int checksum = 0;
    unsigned int result = 0;

    for (unsigned int i = 0; i < ROUNDS; ++i) {
        result = terms[0] - terms[1] + terms[2] - terms[3]
               + terms[4] - terms[5] - terms[6] + terms[7];
        checksum += result ^ i;
    }

    if (tapeout_core_id() == 0) {
        tapeout_puts("Pi benchmark, both cores, 1000000 rounds\n");
        if (result == 3141592652u && checksum == 0xeb2f76e0u) {
            tapeout_puts("pi ~ 3.141592652\nPASS\n");
        } else {
            tapeout_puts("calculation/checksum: FAIL\n");
        }
    }
    return 0;
}
