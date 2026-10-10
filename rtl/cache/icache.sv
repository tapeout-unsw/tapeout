`timescale 1ns / 1ps
//
// Instruction cache. Sits between a core's instruction port and the bus.
// 2-way set associative, 8 sets, one 32-bit word per line.
//
// Parameters
//   RAW     RAM byte address bits. Tag is RAW-5 bits (5 for 1 KiB, 6 for 2 KiB).
//
// Core side
//   i_req    the core wants the instruction at i_addr. i_addr is stable while
//            i_req is high. i_req is also high while the core is held in reset.
//   i_valid  the instruction is on i_rdata this cycle; the core latches it.
//
// Bus side
//   ic_addr  = {i_addr[12:2], 2'b00}.  ic_wdata = 0.  ic_wstrb = 0000 (read).
//   ic_req is held until ic_gnt. bus_rdata is valid the cycle after ic_gnt.
//   While the bootloader is loading, the arbiter grants nobody else, so a
//   request made then simply waits.
//
// Address split
//   index = i_addr[4:2]        one of 8 sets
//   tag   = i_addr[RAW-1:5]
//   line  = {valid, tag, data}; one LRU bit per set names the victim way.
//
// Behaviour
//   IDLE  hit:  i_valid = 1, i_rdata = the hit way's data. Stay in IDLE.
//         i_req and miss: go to MISS.
//   MISS  ic_req = 1. On ic_gnt go to FILL.
//   FILL  write {1, tag, bus_rdata} into the victim way, i_valid = 1,
//         i_rdata = bus_rdata. Go to IDLE.
//   Any hit or fill to way w sets that set's LRU bit to the other way.
//
// flush clears every valid bit and returns the FSM to IDLE. It is high while
// the bootloader is loading a program. It has priority over a fill in the
// same cycle, so no line written during a reload survives it.
//
// My Poor LRU Policy needs to be written

module icache #(
    parameter int RAW = 10
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        flush,

    input  logic        i_req,
    input  logic [12:0] i_addr,
    output logic        i_valid,
    output logic [31:0] i_rdata,

    output logic        ic_req,
    output logic [12:0] ic_addr,
    output logic [31:0] ic_wdata,
    output logic [3:0]  ic_wstrb,
    input  logic        ic_gnt,
    input  logic [31:0] bus_rdata
);
    // Stub: outputs tied off until implemented.
    assign i_valid  = 1'b0;
    assign i_rdata  = '0;
    assign ic_req   = 1'b0;
    assign ic_addr  = '0;
    assign ic_wdata = '0;
    assign ic_wstrb = '0;
endmodule
