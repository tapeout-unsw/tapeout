// SPDX-FileCopyrightText: © 2025 XXX Authors
// SPDX-License-Identifier: Apache-2.0
//
// Pad-facing wrapper around the dual-core Snowy SoC (rtl/soc_top.sv).
//
// Pads
//   clk, rst_n          dedicated clock and active-low reset pads
//   input_in[0]         uart_rx (idle high)
//   bidir[0]            uart_tx      output
//   bidir[1]            done         output, both cores have written EXIT
//   bidir[2]            core_run     output, high once a program has loaded
//   bidir[3]            lock         output, MMIO LOCK register
//   bidir[5:4]          exit[1:0]    output, per-core EXIT flags
//   other bidir pads    driven low
//
// RAW = 11 gives 2 KiB of RAM: four OCD 512x8 macros when built with
// USE_SRAM_MACRO. CLK_FREQ must match the CLOCK_PERIOD in the LibreLane config
// so the UART baud divider is right.

`default_nettype none

module chip_core #(
    parameter NUM_INPUT_PADS,
    parameter NUM_BIDIR_PADS,
    parameter NUM_ANALOG_PADS
    )(
    `ifdef USE_POWER_PINS
    inout  wire VDD,
    inout  wire VSS,
    `endif

    input  wire clk,       // clock
    input  wire rst_n,     // reset (active low)

    input  wire [NUM_INPUT_PADS-1:0] input_in,   // Input value
    output wire [NUM_INPUT_PADS-1:0] input_pu,   // Pull-up
    output wire [NUM_INPUT_PADS-1:0] input_pd,   // Pull-down

    input  wire [NUM_BIDIR_PADS-1:0] bidir_in,   // Input value
    output wire [NUM_BIDIR_PADS-1:0] bidir_out,  // Output value
    output wire [NUM_BIDIR_PADS-1:0] bidir_oe,   // Output enable
    output wire [NUM_BIDIR_PADS-1:0] bidir_cs,   // Input type (0=CMOS Buffer, 1=Schmitt Trigger)
    output wire [NUM_BIDIR_PADS-1:0] bidir_sl,   // Slew rate (0=fast, 1=slow)
    output wire [NUM_BIDIR_PADS-1:0] bidir_ie,   // Input enable
    output wire [NUM_BIDIR_PADS-1:0] bidir_pu,   // Pull-up
    output wire [NUM_BIDIR_PADS-1:0] bidir_pd,   // Pull-down

    inout  wire [NUM_ANALOG_PADS-1:0] analog  // Analog
);

    // Inputs: no pulls. uart_rx idles high, so the board must pull it up.
    assign input_pu = '0;
    assign input_pd = '0;

    // Every bidir pad is an output.
    assign bidir_oe = '1;
    assign bidir_cs = '0;
    assign bidir_sl = '0;
    assign bidir_ie = '0;
    assign bidir_pu = '0;
    assign bidir_pd = '0;

    logic       uart_tx, done, core_run, lock;
    logic [1:0] exit_flags;

    soc_top #(
        .DCACHE(1),
        .NREGS    (16),
        .RAW      (11),
        .CLK_FREQ (50_000_000),
        .BAUD_RATE(115_200)
    ) u_soc_top (
        .clk         (clk),
        .rst         (~rst_n),
        .uart_rx     (input_in[0]),
        .uart_tx     (uart_tx),
        .done        (done),
        .dbg_core_run(core_run),
        .dbg_lock    (lock),
        .dbg_exit    (exit_flags)
    );

    assign bidir_out = {{(NUM_BIDIR_PADS-6){1'b0}}, exit_flags, lock, core_run, done, uart_tx};

    logic _unused;
    assign _unused = &{1'b0, bidir_in, input_in[NUM_INPUT_PADS-1:1]};

endmodule

`default_nettype wire
