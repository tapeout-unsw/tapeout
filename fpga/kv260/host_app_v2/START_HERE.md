# KV260 cache test runner v2

This local experiment uses tapeout `main` commit `e5ecebf`, with both data
caches instantiated (`DCACHE=1`), 1 KiB of SRAM, and a 50 MHz PL clock. It is
separate from the earlier host_app and the `programs` branch UART shell.

## On the Kria

Requires the same KV260 Ubuntu 22.04 / Python 3.10 / PYNQ setup as host_app v1.
Stop any other runner with Ctrl-C, copy this whole folder to
`/home/ubuntu/host_app_v2`, then run:

```sh
cd ~/host_app_v2
bash run.sh
```

Open `http://<kria-ip>:8000`. PYNQ loads the v2 FPGA design, replacing the
currently loaded PL design. To switch back, stop v2 and run `~/host_app/run.sh`
from the v1 folder. The two servers cannot share the board or port at once.

## Chicken bits (cache controls)

Leave all four boxes unchecked for both instruction and both data caches
active. A checked box bypasses that cache for the next program load. The
bootloader receives the mask in bits 31:28: core 0 I-cache, core 1 I-cache,
core 0 D-cache, core 1 D-cache. The app logs and records the mask for each run.
Use only the bundled v2 BIT/HWH: the old bootloader interprets these bits as
part of the length and cannot safely receive a v2 masked header.

| Checkbox | Mask | Header bit | When checked |
| --- | --- | --- | --- |
| Core 0 instruction | `0x8` | 31 | Bypass core 0 I-cache |
| Core 1 instruction | `0x4` | 30 | Bypass core 1 I-cache |
| Core 0 data | `0x2` | 29 | Bypass core 0 D-cache |
| Core 1 data | `0x1` | 28 | Bypass core 1 D-cache |

## How the UART loader works

When **Compile and run** is selected, the Kria's ARM processor compiles the C
source for RV32E, converts the ELF to a flat binary, and forms one UART packet:

```text
32-bit header (4 bytes, little-endian)  |  binary program (N × 4 bytes)
bits 31:28: chicken-bit mask            |  loaded starting at SRAM address 0
bits 27:0:  program length N in words   |
```

The program's binary bytes are unchanged by the cache selections. For example,
a 78-word program has header `0x0000004e` (`4e 00 00 00`) with all caches on.
Checking **both data caches** gives mask `0x3`, so its header becomes
`0x3000004e` (`4e 00 00 30`); the same 312 program bytes follow. Checking all
four gives `0xf000004e` (`4e 00 00 f0`).

The host resets the SoC, then writes the packet to AXI UARTLite. Its TX wire is
connected inside the FPGA to the SoC UART RX and bootloader. The bootloader
collects the first four bytes as the header, stores the bypass bits, then groups
each subsequent four bytes into a word and writes it into SRAM. After the last
word is committed, it releases both cores. Processor UART TX travels back to
AXI UARTLite RX, where the app reads it and streams it into the browser console.
The same header is used in binary mode, but the uploaded binary isn't compiled.

## Suggested tests

Upload a C file from this folder on your computer, check the UART's own PASS or
FAIL message and the runner's exit status. Run each test with all caches on,
both data caches bypassed, each data cache bypassed individually, and all four
bypassed. Repeated runs and other combinations are useful, too.

* `examples/cache_memory.c`: fills and rereads more words than fit in the cache.
* `examples/cache_byte_lanes.c`: warms a line, writes bytes, checks the word.
* `examples/cache_handoff.c`: both cores exchange a shared value under the lock.
* `examples/dual_core_lock.c`: both cores increment a counter under the lock.
* `examples/cache_benchmark.c`: both cores repeatedly read eight shared words;
  checks the computed sums, then prints `cache benchmark: PASS`.

The runner's green Passed means both cores exited, not that the program's
checks passed: inspect the UART message. Results and masks are saved under
`/var/lib/tapeout-host-v2/runs`. UART polling depends on Linux scheduling;
an overrun means output was lost, so retry and investigate rather than treating
the result as a hardware pass.

## Comparing cache performance

The result includes **total** time and a separate **host-observed execution**
time. The latter measures from the first GPIO poll that sees the cores running
until a poll sees both exit. It excludes the initial serial upload and final
quiet wait, but includes startup and program UART output. It is approximate:
Linux scheduling and polling introduce error, and a short program may finish
before the first poll, in which case the app reports `not measurable`. It is
not a cycle counter. The total time includes reset, upload and UART drain, so
it should not be used to infer CPU performance.

To compare, run `cache_benchmark.c` at least five times in each setting and
compare the median host-observed execution time, checking `PASS` each run:

1. All caches enabled (all boxes unchecked).
2. Both instruction caches bypassed, data caches enabled.
3. Both data caches bypassed, instruction caches enabled.
4. All caches bypassed (all boxes checked).

On this RTL's simulation the benchmark took 2,364,981 cycles with all caches
enabled, 2,364,985 with both data caches bypassed, and 3,385,159 with all
caches bypassed. These are **simulated** core-running-to-DONE cycles, not
measured board results. The data-cache comparison reduced bus reads from
320,047 to 39 but barely changed cycles for this workload: a cache hit and an
uncontended SRAM access have similar latency. The instruction-cache bypass has
a much larger cycle impact. These results may change on real hardware.

The present FPGA interface has no hardware cycle or bus-traffic counter, so
the browser cannot prove that 39-versus-320,047 bus-read difference on the
board. Add hardware counters for accurate on-board cache activity and cycles.

The three `cache_*.c` tests were simulated against the full dual-core RTL for
all 16 bypass masks; that does not establish correctness on the FPGA or cover
all cache-coherence edge cases. The hosted web interface has no authentication;
use only on a trusted network.
