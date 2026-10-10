`timescale 1ns / 1ps
//
// One 8-bit SRAM lane. The only file that knows the vendor cell names, pin
// names and active-low polarities; everything above it is active high.
//
// Parameters
//   WORDS   words in this lane. Macro builds support 256, 512 and 1024
//           (gf180mcu_ocd_ip_sram__sram<WORDS>x8m8wm1). The behavioural model
//           accepts any power of two.
//
// Behaviour, sampled on the rising edge of clk
//   en = 1, we = 0   read:  rdata = mem[addr] after the edge
//   en = 1, we = 1   write: mem[addr] = wdata at the edge; rdata unchanged
//   en = 0           idle:  memory and rdata unchanged
//
// Build modes
//   default                  behavioural model, same cycle timing as the macro
//   +define+USE_SRAM_MACRO   the OCD 3.3 V macro. Simulation also needs the
//                            models in ip/gf180mcu_ocd_ip_sram/, synthesis the
//                            __blackbox.v files. The macro's VDD/VSS pins only
//                            exist under USE_POWER_PINS and are connected by
//                            the physical-design flow, not here.
//
module sram_macro #(
    parameter int WORDS = 512,
    parameter int AW    = $clog2(WORDS)
) (
    input  logic          clk,
    input  logic          en,                  // access this cycle
    input  logic          we,                  // 1 = write, 0 = read
    input  logic [AW-1:0] addr,                // row
    input  logic [7:0]    wdata,               // byte to write
    output logic [7:0]    rdata                // valid the cycle after a read
);

    // Stub: output tied off until implemented.
    assign rdata = '0;

endmodule