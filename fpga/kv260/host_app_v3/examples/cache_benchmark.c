#include "tapeout.h"

// Both cores repeatedly read eight words that fit in each D-cache. Core 0
// checks both results. No UART output occurs during the measured loops.
// Expected: "cache benchmark: PASS".
// Compare host-observed execution times over repeated runs with data caches
// on versus bypassed; the host measurement is approximate, not cycle-accurate.
volatile unsigned int data[8] = {1, 2, 3, 4, 5, 6, 7, 8};
volatile unsigned int core1_result = 0;
volatile unsigned int core1_done = 0;

int main(void) {
    unsigned int id = tapeout_core_id();
    unsigned int sum = 0;
    for (unsigned int round = 0; round < 20000; ++round) {
        for (unsigned int i = 0; i < 8; ++i) sum += data[i];
    }
    if (id == 1) {
        tapeout_lock();
        core1_result = sum;
        core1_done = 1;
        tapeout_unlock();
    } else {
        unsigned int done = 0;
        unsigned int other = 0;
        while (!done) {
            tapeout_lock();
            done = core1_done;
            other = core1_result;
            tapeout_unlock();
        }
        tapeout_puts(sum == 720000u && other == 720000u
            ? "cache benchmark: PASS\n" : "cache benchmark: FAIL\n");
    }
    return 0;
}
