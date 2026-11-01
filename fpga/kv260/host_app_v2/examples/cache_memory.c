#include "tapeout.h"

// Core 0 fills and rereads 24 words (more than one cache's 16 lines).
// Expected: "memory fill/read: PASS" in every cache configuration.
volatile unsigned int words[24];

int main(void) {
    if (tapeout_core_id() != 0) return 0;
    unsigned int failed = 0;
    for (unsigned int i = 0; i < 24; i++) words[i] = i + 100;
    for (unsigned int round = 0; round < 3; round++) {
        for (unsigned int i = 0; i < 24; i++) {
            if (words[i] != i + 100) failed = 1;
        }
    }
    tapeout_puts(failed ? "memory fill/read: FAIL\n" : "memory fill/read: PASS\n");
    return 0;
}
