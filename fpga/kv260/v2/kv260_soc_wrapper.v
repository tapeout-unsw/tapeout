`timescale 1ns / 1ps
//
// FPGA-only wrapper around the tapeout SoC.
//
// AXI UARTLite is the host UART: its TX output drives host_uart_tx and its RX
// input receives host_uart_rx. The processor therefore still uses its real
// uart_rx, bootloader and uart_tx logic; the serial wire is just inside the PL.
//
// software_reset and the status outputs are connected to AXI GPIO so software
// on the KV260 processing system can reset and inspect the processor.
//
// This wrapper deliberately uses Verilog-2001 because Vivado IP Integrator
// requires a Verilog top file for a module-reference block. The SoC beneath it
// remains SystemVerilog.
//
module kv260_soc_wrapper #(
    parameter NREGS     = 16,
    parameter RAW       = 10,
    parameter CLK_FREQ  = 50_000_000,
    parameter BAUD_RATE = 115_200
) (
    input  wire       pl_clk,
    input  wire       pl_resetn,
    input  wire       software_reset,

    input  wire       host_uart_tx,
    output wire       host_uart_rx,

    output wire       done,
    output wire       dbg_core_run,
    output wire       dbg_lock,
    output wire [1:0] dbg_exit
);
    // soc_top uses an active-high synchronous reset. Keep it asserted for two
    // complete PL clock cycles after the AXI fabric reset is released.
    reg  [1:0] reset_pipe;
    wire       soc_reset;

    always @(posedge pl_clk) begin
        if (!pl_resetn)
            reset_pipe <= 2'b11;
        else
            reset_pipe <= {reset_pipe[0], 1'b0};
    end

    assign soc_reset = reset_pipe[1] | software_reset;

    soc_top #(
        .NREGS     (NREGS),
        .RAW       (RAW),
        .CLK_FREQ  (CLK_FREQ),
        .BAUD_RATE (BAUD_RATE),
        .DCACHE    (1)
    ) u_soc (
        .clk          (pl_clk),
        .rst          (soc_reset),
        .uart_rx      (host_uart_tx),
        .uart_tx      (host_uart_rx),
        .done         (done),
        .dbg_core_run (dbg_core_run),
        .dbg_lock     (dbg_lock),
        .dbg_exit     (dbg_exit)
    );
endmodule
