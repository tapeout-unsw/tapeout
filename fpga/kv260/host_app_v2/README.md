# Standalone KV260 program runner

> **Local v2 cache-test build.** For this version's setup, cache switches,
> tests and limitations, read [START_HERE.md](START_HERE.md). This is separate
> from the original host_app. Do not use the original bitstream with v2.

## Run it on the board

Copy this entire folder to `/home/ubuntu/host_app_v2`, then run one command as the
normal `ubuntu` user:

```sh
cd /home/ubuntu/host_app_v2
bash run.sh
```

The script verifies the corrected bitstream and matching HWH, installs all
bundled dependencies without internet access when needed, performs a safe PYNQ
overlay load with no AXI/MMIO transactions, and starts the web server. Leave
the terminal open while using the interface and press Ctrl-C to stop it.

This PYNQ flow uses `system_wrapper.bit` plus `system_wrapper.hwh`. There is no
`.bin` in this folder by design, and `fpgautil` must not be run first.

This application runs on Ubuntu on the KV260 processing system (PS). It loads
and observes the separate dual-core RV32E processor built into programmable
logic (PL). It is a normal web application, not a Jupyter notebook. PYNQ is
used underneath the app to configure the PL clocks, load the overlay and map
the AXI peripherals safely.

Open the application from any browser on the same network. It provides:

- a source mode that compiles one uploaded `.c`, `.S` or `.s` file for RV32E;
- a binary mode that transmits an uploaded `.bin` file without modifying it;
- a live processor UART console and live `core_run`, lock, exit and done state;
- a permanent run directory containing the input, exact loaded binary,
  compiler log, UART bytes, SHA-256 digest and JSON result.

## Hardware contract

Place the matching Vivado products beside each other in the application root:

```text
system_wrapper.bit
system_wrapper.hwh
```

The HWH is generated as `system.hwh` under
`<project>.gen/sources_1/bd/system/hw_handoff/`; rename the copy to
`system_wrapper.hwh` so that its base name matches the bitstream. PYNQ parses
that file, configures the PS-to-PL clock, programs the PL, and discovers these
AXI peripherals:

| Address | Block | Use |
| --- | --- | --- |
| `0xA0000000` | AXI UARTLite | bootloader input and processor UART output |
| `0xA0010000` | AXI GPIO | software reset and processor status |

Do not load the design separately with `fpgautil`: the application loads it
through PYNQ. Do not run another program that accesses these AXI blocks at the
same time.

## What the launcher does

`run.sh` is safe to run again after the first setup. It:

1. verifies that the BIT and HWH are the tested matching pair;
2. installs the bundled ARM64 Python packages and RISC-V compiler when absent;
3. loads and parses the overlay without issuing an AXI transaction; and
4. starts the web server and hardware interface after that smoke test passes.

No `apt`, `pip`, internet connection, virtual environment or separate FPGA
programming command is required. The script asks for the `sudo` password when
PYNQ needs hardware access, but the script itself must be run as `ubuntu` so
the generated `vendor` and `toolchain` directories remain user-owned.

From a computer on the same network, open:

```text
http://<kv260-ip-address>:8000
```

Run records are stored under `/var/lib/tapeout-host-v2/runs`. The server listens
on all interfaces and currently has no authentication, so use it only on a
trusted lab network.

## Compile-source mode

The included runtime links at address zero for RV32E/ILP32E. Both cores call
`main()`. It reserves separate 64-byte stacks and makes the last 128 bytes of
the 1 KiB SRAM unavailable to code/data, so a compiled program must fit below
`0x380` (896 bytes including BSS).

An uploaded C program can include `tapeout.h` and use:

```c
uint32_t tapeout_core_id(void);
void tapeout_putchar(char value);
void tapeout_puts(const char *text);
void tapeout_lock(void);
void tapeout_unlock(void);
void tapeout_exit(void);
```

Both cores run the same `main()`. Check `tapeout_core_id()` when only one core
should print or perform a task. Returning from `main()` sets the calling core's
EXIT bit. The run finishes after both cores return. Start with
`examples/hello.c`.

This processor implements RV32E without the multiply/divide extension or an
operating system. Uploaded C is freestanding: standard library functions such
as `printf`, files and dynamic allocation are not available. The compiler log
shows unsupported operations or images that exceed SRAM.

Uploaded assembly source participates in the included runtime and must export
`main`, not `_start`. To load a complete hand-written image with its own
`_start`, build it separately and select binary mode.

## Binary mode

Binary mode accepts a flat, little-endian image for address zero. It must be:

- non-empty;
- no larger than 1024 bytes; and
- an exact multiple of four bytes.

The application does not pad, rewrite or reinterpret the file. It prepends
only the bootloader's four-byte little-endian word count while transmitting.

## Run software tests

The standard-library tests do not touch `/dev/mem`:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
