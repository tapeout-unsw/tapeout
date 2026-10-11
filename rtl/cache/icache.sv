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
    localparam int TAGW = RAW - 5;

    // ---------------- Address split ----------------
    logic [2:0]      index;
    logic [TAGW-1:0] tag;
    assign index = i_addr[4:2];
    assign tag   = i_addr[RAW-1:5];

    // ---------------- Storage ----------------
    logic [31:0]     sec0_data,  sec1_data;
    logic [TAGW-1:0] sec0_tag,   sec1_tag;
    logic            sec0_valid, sec1_valid;
    logic            sec0_write_en, sec1_write_en;

    icache_storage u_storage (
        .clk             (clk),
        .reset           (rst),
        .index           (index),

        .sec0_write_en   (sec0_write_en),
        .sec0_write_data (bus_rdata),
        .sec0_write_tag  (tag),

        .sec1_write_en   (sec1_write_en),
        .sec1_write_data (bus_rdata),
        .sec1_write_tag  (tag),

        .sec0_data       (sec0_data),
        .sec1_data       (sec1_data),
        .sec0_tag        (sec0_tag),
        .sec1_tag        (sec1_tag),
        .sec0_valid      (sec0_valid),
        .sec1_valid      (sec1_valid)
    );

    // ---------------- Hit detection ----------------
    logic hit0, hit1, cache_hit, hit_way;
    assign hit0      = sec0_valid && (sec0_tag == tag);
    assign hit1      = sec1_valid && (sec1_tag == tag);
    assign cache_hit = hit0 || hit1;
    assign hit_way   = hit1;

    // ---------------- FSM ----------------
    logic serve_hit, miss_start, fill;

    icache_FSM u_fsm (
        .clk        (clk),
        .rst        (rst),
        .flush      (flush),
        .i_req      (i_req),
        .cache_hit  (cache_hit),
        .ic_gnt     (ic_gnt),
        .serve_hit  (serve_hit),
        .miss_start (miss_start),   // not used yet
        .ic_req     (ic_req),
        .fill       (fill)
    );

    // ---------------- LRU ----------------
    logic victim_way;

    icache_lru u_lru (
        .clk        (clk),
        .rst        (rst),
        .index      (index),
        .access     (serve_hit || fill),
        .access_way (fill ? victim_way : hit_way),
        .victim_way (victim_way)
    );

    // ---------------- Fill ----------------
    assign sec0_write_en = fill && (victim_way == 1'b0);
    assign sec1_write_en = fill && (victim_way == 1'b1);

    // ---------------- Core side ----------------
    assign i_valid = serve_hit || fill;
    assign i_rdata = fill ? bus_rdata
                   : (hit1 ? sec1_data : sec0_data);

    // ---------------- Bus side ----------------
    assign ic_addr  = {i_addr[12:2], 2'b00};
    assign ic_wdata = 32'b0;
    assign ic_wstrb = 4'b0000;
endmodule