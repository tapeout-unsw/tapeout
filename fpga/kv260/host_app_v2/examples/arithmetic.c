#include "tapeout.h"

// Core 0 checks basic arithmetic and branches; core 1 simply returns.
// UART prints each check and a final PASS or FAIL.
int main(void) {
    if (tapeout_core_id() != 0) {
        return 0;
    }

    // Volatile values make the processor do the calculations at runtime.
    volatile unsigned int a = 37;
    volatile unsigned int b = 12;

    unsigned int failed = 0;
    tapeout_puts("Arithmetic test, core 0\n");
    tapeout_puts("Inputs: a=37, b=12\n");

    if (a + b == 49 && a - b == 25) {
        tapeout_puts("Add/sub: 49, 25 - PASS\n");
    } else {
        tapeout_puts("Add/sub: FAIL\n");
        failed = 1;
    }

    if ((a & b) == 4 && (a | b) == 45 && (a ^ b) == 41) {
        tapeout_puts("AND/OR/XOR: 4, 45, 41 - PASS\n");
    } else {
        tapeout_puts("Bitwise: FAIL\n");
        failed = 1;
    }

    if ((a << 1) == 74 && (a >> 2) == 9 && a > b) {
        tapeout_puts("Shifts: 74, 9; a > b - PASS\n");
    } else {
        tapeout_puts("Shift/branch: FAIL\n");
        failed = 1;
    }
    tapeout_puts(failed ? "Arithmetic: FAIL\n" : "Arithmetic: PASS\n");
    return 0;
}
