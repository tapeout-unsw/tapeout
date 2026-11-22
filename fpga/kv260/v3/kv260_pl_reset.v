`timescale 1ns / 1ps
//
// Configuration-start reset for the FPGA-side AXI fabric.
//
// Linux programs only the PL bitstream; it does not rerun the Vivado PS
// initialization code. Depending on the boot image, the PS pl_resetn output
// can therefore remain asserted and hold every AXI slave in reset forever.
// A PS access to such a slave never completes and can wedge Linux and SSH.
//
// Xilinx FPGAs load the initial value below during configuration. The reset is
// held low for 32 PL clocks and then deasserted synchronously. Reprogramming
// the bitstream automatically rearms it, without relying on a PS reset output.
//
module kv260_pl_reset (
    input  wire clk,
    output wire resetn
);
    (* SHREG_EXTRACT = "NO" *) reg [31:0] startup = 32'hFFFF_FFFF;

    always @(posedge clk)
        startup <= {startup[30:0], 1'b0};

    assign resetn = ~(|startup);
endmodule
