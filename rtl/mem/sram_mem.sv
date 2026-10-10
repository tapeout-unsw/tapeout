`timescale 1ns / 1ps
//
// SRAM controller: the 32-bit memory the bus sees, built from four 8-bit
// lanes, one sram_macro per byte. Lane i holds bits [8i+7:8i], the byte at
// offset +i in every word (little-endian).
//
// Parameters
//   WORDS   words per lane: 256 = 1 KiB, 512 = 2 KiB, 1024 = 4 KiB.
//
// Interface
//   Word-addressed. wstrb selects which bytes are written; wstrb = 0 is a read.
//   Every lane is enabled on every access and lane i writes only if wstrb[i],
//   so lanes not written by a partial store do a read the bus ignores.
//   Reads: rdata is valid one cycle after en. Writes complete at the rising
//   edge that ends the cycle in which en is high.
//   There are no registers here: the macros are the only clocked elements.
//
module sram_mem #(
    parameter int WORDS = 512,                 // 512 words x 32b = 2 KB
    parameter int AW    = $clog2(WORDS)
) (
    input  logic          clk,
    input  logic          en,                  // perform an access this cycle
    input  logic [AW-1:0] addr,                // WORD address, not byte
    input  logic [31:0]   wdata,
    input  logic [3:0]    wstrb,               // per-byte write enable; 0 = read
    output logic [31:0]   rdata                // valid one cycle after en
);
    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_lane
            sram_macro #(.WORDS(WORDS)) u_lane (
                .clk  (clk),
                .en   (en),
                .we   (wstrb[i]),
                .addr (addr),
                .wdata(wdata[8*i +: 8]),
                .rdata(rdata[8*i +: 8])
            )
        end
    endgenerate
endmodule
