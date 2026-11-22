#include "tapeout.h"

// Both cores increment one shared counter under the hardware lock. Core 0
// waits for core 1, checks the result, and is the only core that prints.
// Core 0 describes the test and prints whether the final count is correct.
// A timeout can indicate that the lock or core-to-core communication stalled.
volatile unsigned int counter = 0;
volatile unsigned int core1_done = 0;

int main(void) {
    unsigned int id = tapeout_core_id();

    if (id == 0) {
        tapeout_puts("Dual-core shared counter test\n");
        tapeout_puts("Core 0 + core 1: 20 increments each\n");
        tapeout_puts("Expected final count: 40\n");
    }

    // Each core contributes 20 increments. Protect the entire read/modify/
    // write sequence, so the expected final count is exactly 40.
    for (unsigned int i = 0; i < 20; ++i) {
        tapeout_lock();
        counter = counter + 1;
        tapeout_unlock();
    }

    if (id == 1) {
        tapeout_lock();
        core1_done = 1;
        tapeout_unlock();
    } else {
        unsigned int done = 0;
        while (!done) {
            tapeout_lock();
            done = core1_done;
            tapeout_unlock();
        }
        if (counter == 40) {
            tapeout_puts("Both cores contributed; count is 40\n");
            tapeout_puts("Hardware lock: PASS\n");
        } else {
            tapeout_puts("Hardware lock: FAIL (wrong count)\n");
        }
    }
    return 0;
}
