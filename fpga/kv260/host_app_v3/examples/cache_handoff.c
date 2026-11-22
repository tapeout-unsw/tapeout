#include "tapeout.h"

// Two-core shared-memory handoff. Core 0 warms a shared value, signals core 1
// to replace it, then checks the new value. Every shared access is protected by
// the hardware lock. Expected: "cache handoff: PASS" for every cache setting.
volatile unsigned int shared = 7;
volatile unsigned int phase = 0;

int main(void) {
    unsigned int id = tapeout_core_id();
    unsigned int ready = 0;
    if (id == 0) {
        tapeout_lock();
        ready = shared;
        phase = 1;
        tapeout_unlock();
        if (ready != 7) {
            tapeout_puts("cache handoff: FAIL initial\n");
            return 0;
        }
        ready = 0;
        while (!ready) {
            tapeout_lock();
            ready = phase == 2;
            tapeout_unlock();
        }
        tapeout_lock();
        ready = shared;
        tapeout_unlock();
        tapeout_puts(ready == 42 ? "cache handoff: PASS\n" : "cache handoff: FAIL stale\n");
    } else {
        while (!ready) {
            tapeout_lock();
            ready = phase == 1;
            tapeout_unlock();
        }
        tapeout_lock();
        shared = 42;
        phase = 2;
        tapeout_unlock();
    }
    return 0;
}
