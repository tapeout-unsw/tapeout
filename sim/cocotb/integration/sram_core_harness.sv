`timescale 1ns / 1ps
//
// Test harness, not RTL: the core's memory path for one core with the bus
// taken out. The test plays the core's S_MEM and S_MEM_W states.
//
//   test → mem_access (positions store bytes, makes wstrb)
//        → sram_mem   (stores, returns the whole word one cycle later)
//        → mem_access (picks the byte or halfword and extends it) → load_data
//
// Inputs mirror what core.sv drives from IR and the ALU: they stay constant
// through S_MEM and S_MEM_W, so load_data is read during S_MEM_W.
//
module sram_core_harness #(
    parameter int WORDS = 512,                 // words per lane
    parameter int AW    = $clog2(WORDS)
) (
    input  logic          clk,
    input  logic          en,                  // S_MEM: the access is granted this cycle
    input  logic          mem_write,           // store (1) or load (0)
    input  logic [2:0]    func3,               // RV32 width: lb lh lw lbu lhu / sb sh sw
    input  logic [AW+1:0] byte_addr,           // byte address inside the SRAM
    input  logic [31:0]   store_data,          // rs2
    output logic [31:0]   load_data,           // valid in S_MEM_W
    output logic [3:0]    wstrb,               // what mem_access sent to the SRAM
    output logic [31:0]   wdata                // likewise
);
    logic [31:0] rdata;
    logic [3:0]  w_strb;

    mem_access u_mem_access (
        .func3     (func3),
        .addr_lo   (byte_addr[1:0]),
        .mem_write (mem_write),
        .store_data(store_data),
        .w_data    (wdata),
        .w_strb    (w_strb),
        .raw_rdata (rdata),
        .load_data (load_data)
    );

    assign wstrb = mem_write ? w_strb : 4'b0000;   // as core.sv drives d_wstrb

    sram_mem #(.WORDS(WORDS)) u_sram (
        .clk  (clk),
        .en   (en),
        .addr (byte_addr[AW+1:2]),
        .wdata(wdata),
        .wstrb(wstrb),
        .rdata(rdata)
    );
endmodule
