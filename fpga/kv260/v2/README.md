# KV260 data-cache FPGA experiment

`../host_app_v2/START_HERE.md` documents board setup, chicken bits, UART header,
and tests. This build instantiates both data caches (`DCACHE=1`), uses 1 KiB
SRAM and a 50 MHz PL clock. It is independent of the original v1 FPGA build.

`rtl/` is an unmodified snapshot of repository `main` commit `e5ecebf`, kept
here so this v2 image can be rebuilt without changing the original branch's
processor RTL. The local `kv260_soc_wrapper.v` enables its data caches.

From the repository root, with Vivado and KV260 board files installed:

```sh
vivado -mode batch -source fpga/kv260/v2/create_project.tcl -tclargs --build
```

This FPGA build uses generic inferred SRAM rather than the GF180 macro. A new
build needs matching BIT/HWH files and updated hashes in `../host_app_v2/run.sh`.

`sim/cache_mask_soc_top_tb.sv` is the testbench variation used for the v2 C
examples (`RAW=10`, `DCACHE=1`, `+BYPASS=<0..15>`). It verifies the captured
chicken bits and reports simulated execution cycles and data-bus read grants.
It is not evidence that a test passed on the physical board.
