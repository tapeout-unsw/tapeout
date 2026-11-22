# KV260 web app v3

This app uses the Kria PS and its AXI UARTLite to load programs into the
processor's hardware UART bootloader. It is based on main `9dffecc` plus the
local fix connecting the AMOSWAP chicken bit to both cores. The FPGA build has
2 KiB RAM, both instruction caches and both data caches.

Copy the folder to `/home/ubuntu/host_app_v3` on the Kria. Stop the old web app
with Ctrl-C, then run `cd ~/host_app_v3 && bash run.sh`. The first run installs
the included Python wheels and ARM64 RISC-V toolchain locally without internet.
The script programs the included bitstream and checks the 50 MHz clock. On a
computer connected to the board, open `http://<kria-ip>:8000/`. Stopping the
server does not restore another bitstream; run that project's usual loader.

Choose a C or assembly source, or an existing flat `.bin`. The cache checkboxes
use bits 31:28 in the header (I0, I1, D0, D1); checked means bypassed. The
atomic checkbox uses bit 27; checked disables AMOSWAP for both cores. The
remaining bits hold the word count. Header and image words are sent
little-endian over the PS UARTLite to the PL UART bootloader. A raw binary
must not contain its own boot header.

Try `examples/pi_cache_benchmark.c`: it sums a fixed-point, precomputed Machin
series for one million iterations on both cores and checks a checksum. Compare
all caches enabled versus all four bypassed, repeating each run. The displayed
times are approximate host observations, not processor cycle counts.

`examples/atomic_demo.S` prints `E` when AMOSWAP is enabled and works, `D`
when disabled, or `F` for an unexpected result. The compiler accepts RV32EA
assembly so this instruction can be assembled, but the RTL implements
**AMOSWAP only**, not the entire RISC-V A extension. Other atomics and LR/SC
must not be assumed to work.

The status `Completed` means both cores wrote EXIT. For self-checking programs,
also check the UART console for their own PASS/FAIL result.
