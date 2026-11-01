#include "tapeout.h"

// This program runs on both FPGA-resident RV32E cores. Core 0 prints a message;
// both cores then return from main, which makes the runtime set both EXIT bits.
// Expected UART output: hello from the FPGA\n
int main(void) {
    if (tapeout_core_id() == 0) {
        tapeout_puts("hello from the FPGA\n");
    }
    return 0;
}
