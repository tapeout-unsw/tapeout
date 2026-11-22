# KV260 v3 board verification

The PS-to-PL web app was tested on the KV260 with its v3 bitstream loaded.
The frontend supplied the chicken bits through its run API, and source files
were compiled by the bundled ARM64 GCC on the board.

- `atomic_demo.S` passed for every one of the 32 cache/atomic combinations.
  It emitted `E` with AMOSWAP enabled and `D` with AMOSWAP disabled. Each run
  completed with both EXIT bits set. This demo checks the instruction on core
  0; the earlier direct-UART suite separately checked both cores and contention.
- `pi_cache_benchmark.c` passed three times with all caches enabled and three
  times with all caches bypassed. The returned UART text included
  `pi ~ 3.141592652` and `PASS` each time.
- Approximate host-observed execution times: all enabled 1.2853, 1.2852,
  1.2853 seconds; all bypassed 1.5057, 1.5050, 1.5052 seconds. These are
  software-sampled durations, not cycle counts. Total run times include
  loading and UART overhead and were about 1.397 and 1.617 seconds respectively.
- Vivado's routed report showed all user-specified timing constraints met,
  with WNS 10.970 ns and WHS 0.021 ns.
- The web app's 21 unit tests passed, including all 32 boot-header combinations
  and the atomic checkbox request path. The bundled installer checksums passed.

The FPGA source snapshot is based on main `9dffecc` with a local fix wiring the
bootloader's atomic chicken bit to both cores. It was not pushed upstream.
