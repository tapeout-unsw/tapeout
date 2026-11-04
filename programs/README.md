# Programs

## Resident OS

`os.S` runs a UART shell continuously on core 0. Core 1 executes the RV32E
instruction words entered in the shell. The OS fits the default 1 KiB SRAM
and also runs with 2 KiB SRAM, with or without data caches. The hardware does
not fix these roles: assembling with `--defsym SHELL_CORE=1` runs the shell on
core 1 and payloads on core 0. The text below describes the default build.

Build and test inside `nix develop`:

```sh
make hex/os.hex
make test-os
make test-os NREGS=32 DCACHE=1 RAW=11
```

Reset the board, then upload the OS and open its console:

```sh
python3 os_console.py /dev/ttyUSB0 --image bin/os.bin
```

The helper uses Python's standard library on Linux/macOS. It sends the existing
binary boot protocol, a little-endian word count followed by the binary image,
then opens a line-oriented terminal at 115200 baud, 8N1. Your terminal provides
local echo and line editing. To reconnect to an already running OS, omit
`--image`. With another serial terminal, enable local echo and send CR or LF.
Wait for `hex> ` before entering instructions.

Below should output 'A' from UART:

```text
hex> 000012b7 0042a303 00137313 fe031ce3 04100313 0062a023
```

This executes `addi a0, zero, 42` and `sw a0, 0x3fc(zero)` on core 1. The OS
appends `ret`. After the snippet returns, core 1 parks and core 0 keeps reading
the console. The shell does not print register or memory results. A payload
may use UART MMIO for output, but it must coordinate with the shell's output.

Each new hex line replaces the previous job. The first hex digit stops core 1
and flushes its caches before the OS writes instructions. Send `!` to stop an
infinite loop or cancel input. The serial helper is line buffered, so type `!`
and press Enter; the shell ignores the trailing newline. An empty line does nothing. Invalid characters, incomplete words, more than
47 words, and RX overflow produce `ERR` and leave core 1 stopped. An invalid
line is discarded through its newline. The shell accepts upper/lowercase hex
and CR, LF, or CRLF. Do not include `0x` prefixes or comments.

The memory layout is fixed for both RAM sizes:

| Address | Use |
|---|---|
| `0x000..0x2ff` | OS code, strings and job state |
| `0x300..0x3bb` | Up to 47 payload instructions |
| `0x3bc..0x3bf` | Space for the appended return instruction |
| `0x3c0..0x3ff` | 64-byte payload stack, initial `sp = 0x400` |

Core 1 starts at `0x300` with `ra` pointing to the OS worker's return handler.
Save and restore `ra` if your payload calls functions. Use only `x0..x15` on
RV32E. Other registers contain worker startup values and are not an ABI.
Branches and jumps must target the payload's actual addresses. The hardware
has no memory protection, so payload stores can overwrite the OS and core 1
can access peripherals. The resident-shell guarantee assumes the payload
respects the reserved memory and UART RX registers.

The hardware support is generic: any program on either core can claim UART RX
and hold or release the other core. While CONTROL bit 0 is set, the bootloader
ignores incoming bytes and a 16-byte RX FIFO supplies software. Board reset
restores binary boot mode. Existing programs that never claim RX retain the
original back-to-back binary reload behavior.

| Register | Meaning |
|---|---|
| `0x1004` | Bit 0 TX busy, bit 1 RX available, bit 2 sticky RX overflow |
| `0x1018` | Read pops one RX byte, or returns zero when empty |
| `0x101c` | CONTROL, writable by either core (bits below) |

| CONTROL bit | Name | Meaning |
|---|---|---|
| 0 | `RX_CLAIM` | Software owns RX instead of the bootloader |
| 1 | `HOLD0` | Core 0 held in reset with its caches flushed |
| 2 | `HOLD1` | Core 1 held in reset with its caches flushed |
| 3 | `OVF_CLR` | Write 1 to clear RX overflow; reads 0 |

A core's write to its own HOLD bit is ignored, so a core cannot stop itself
and both cores can never be held at once. A released core restarts at address
0, so software chooses its role by reading CORE_ID. The default shell writes
`5` to claim RX and hold core 1, `1` to release it, and `13` to also clear
overflow. Core reset through the bootloader clears the FIFO and CONTROL. This shell runs indefinitely, so use `make test-os`
instead of `make run PROGRAM=os`, which waits for both cores to exit.

## Assembly checks

```sh
# Build all assembly programs
make

# Run the list of program checks
./test_all.sh
NREGS=32 ./test_all.sh        # Use 32 registers instead of the default 16

# Build and run one program
make run PROGRAMS=mc_memwalk

# Load programs and print UART output without comparing it
make run PROGRAMS="mc_reload_a mc_reload_b"

# Load two programs in order, without resetting the SoC between them
make run PROGRAMS="mc_reload_a mc_reload_b" UART=AP

# Load three programs, including a repeated image
make run PROGRAMS="mc_memwalk mc_uart mc_memwalk" UART=PHELLOWORLDPP

# Assembly filenames are also accepted
make run PROGRAMS="mc_reload_a.S mc_reload_b.S" UART=AP

# Inspect register values after every instruction
make run PROGRAMS=write_regs SIM_ARGS='+TRACE_REGS=1'

# Check selected registers on both cores
make run PROGRAMS=write_regs \
    REGS="0:x5=000000ff 1:x5=0000ff00"
make run PROGRAMS=memory REGS="0:x10=ffffff80 1:x11=00000080"

# Remove generated files
make clean
```
