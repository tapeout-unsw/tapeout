# KV260 FPGA project

This flow places the dual-core tapeout SoC in the KV260 programmable logic and
lets the Linux application on the processing system load and test it.

The serial path remains intact:

```text
PS AXI -> AXI UARTLite TX -> soc_top.uart_rx -> bootloader -> processor
PS AXI <- AXI UARTLite RX <- soc_top.uart_tx <- MMIO
```

AXI GPIO channel 1 controls the SoC reset. Channel 2 exposes `done`,
`core_run`, the hardware lock and both exit bits.

## Prerequisites

- Vivado with the KV260 board files installed.
- A Windows-visible checkout path if using Windows Vivado. A mapped WSL path is
  preferable to opening the project through a `\\wsl.localhost` UNC path.

## Create the project

From the repository root:

```sh
vivado -mode batch -source fpga/kv260/create_project.tcl
```

The generated project is `fpga/kv260/build/kv260_riscv.xpr`.

To create the project and build a bitstream:

```sh
vivado -mode batch -source fpga/kv260/create_project.tcl -tclargs --build
```

Pass another directory after `-tclargs` to put the generated project elsewhere:

```sh
vivado -mode batch -source fpga/kv260/create_project.tcl \
  -tclargs D:/vivado-build/kv260-riscv --build
```

## AXI address map

| Address | Peripheral | Purpose |
| --- | --- | --- |
| `0xA0000000` | AXI UARTLite | Sends/receives the processor boot UART stream |
| `0xA0010000` | AXI GPIO | Channel 1 reset output, channel 2 status input |

GPIO channel 1 bit 0 is the active-high software reset.

GPIO channel 2 is:

| Bits | Value |
| --- | --- |
| `[0]` | `done` |
| `[1]` | `core_run` |
| `[2]` | hardware lock state |
| `[4:3]` | core exit flags |

## FPGA memory

`USE_SRAM_MACRO` is deliberately not defined and the GF180 SRAM models are not
added to the project. Vivado therefore sees the generic synchronous memory in
`rtl/mem/sram_macro.sv` and infers FPGA memory.

After implementation, inspect `build/reports/utilization.rpt` to see whether
Vivado selected block RAM or distributed RAM. No FPGA-specific RAM attribute is
forced in this first version.

## Standalone PS application

[`host_app/README.md`](host_app/README.md) describes the Ubuntu application for
compiling uploaded C/assembly as RV32E or loading an existing raw binary,
sending it through AXI UARTLite, and watching the live processor UART and GPIO
status in a browser. It uses the board's existing PYNQ installation to load
the design and does not require Jupyter.
