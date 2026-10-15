`timescale 1ns / 1ps
//
// Instruction cache. Sits between a core's instruction port and the bus.
// 2-way set associative, 8 sets, one 32-bit word per line.
//
// Parameters
//   RAW     RAM byte address bits. Kept for top-level parameter compatibility.
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
//   tag   = i_addr[10:5]       six tag bits
//   line  = {valid, tag, data}; one LRU bit per set names the victim way.
//
// Behaviour
//   IDLE  hit:  i_valid = 1, i_rdata = the hit way's data. Stay in IDLE.
//         i_req and miss: go to MISS.
//   MISS  ic_req = 1. On ic_gnt go to FILL.
//   FILL  write {1, tag, bus_rdata} into the victim way, i_valid = 1,
//         i_rdata = bus_rdata. Go to IDLE.
//   A fill to way w sets that set's LRU bit to the other way. Hits leave it
//   unchanged, so the victim is the way filled longest ago.
//
// flush returns the FSM to IDLE, clears a saved miss and clears every valid
// bit (it drives the storage reset together with rst). It is high while the
// bootloader loads a program, so no line from the old program survives.

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
    localparam int TAGW = 6;

    // ---------------- Address split ----------------
    logic [2:0]      index;
    logic [TAGW-1:0] tag;
    assign index = i_addr[4:2];
    assign tag   = i_addr[10:5];

    // Save the whole miss transaction. The core address can move while the
    // arbiter and one-cycle SRAM read are completing the request.
    logic [12:0]     miss_addr;
    logic [2:0]      miss_index;
    logic [TAGW-1:0] miss_tag;
    logic            miss_victim;
    logic            miss_pending;
    logic            victim_way;    // from icache_lru, declared before its first use
    logic [2:0]      storage_index;
    assign storage_index = miss_pending ? miss_index : index;

    // ---------------- Storage ----------------
    logic [31:0]     sec0_data,  sec1_data;
    logic [TAGW-1:0] sec0_tag,   sec1_tag;
    logic            sec0_valid, sec1_valid;
    logic            sec0_write_en, sec1_write_en;

    icache_storage u_storage (
        .clk             (clk),
        .reset           (rst | flush),      // flush clears every line
        .index           (storage_index),

        .sec0_write_en   (sec0_write_en),
        .sec0_write_data (bus_rdata),
        .sec0_write_tag  (miss_tag),

        .sec1_write_en   (sec1_write_en),
        .sec1_write_data (bus_rdata),
        .sec1_write_tag  (miss_tag),

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
        .miss_start (miss_start),
        .ic_req     (ic_req),
        .fill       (fill)
    );

    // ---------------- Saved miss ----------------
    always_ff @(posedge clk) begin
        if (rst || flush) begin
            miss_addr    <= 13'b0;
            miss_index   <= 3'b0;
            miss_tag     <= '0;
            miss_victim  <= 1'b0;
            miss_pending <= 1'b0;
        end else begin
            if (miss_start) begin
                miss_addr    <= {i_addr[12:2], 2'b00};
                miss_index   <= index;
                miss_tag     <= tag;
                miss_victim  <= victim_way;
                miss_pending <= 1'b1;
            end
            if (fill)
                miss_pending <= 1'b0;
        end
    end

    // ---------------- LRU ----------------

    icache_lru u_lru (
        .clk        (clk),
        .rst        (rst),
        .index      (storage_index),
        .access     (fill),             // hits don't update the LRU bit
        .access_way (fill ? miss_victim : hit_way),
        .victim_way (victim_way)
    );

    // ---------------- Fill ----------------
    assign sec0_write_en = fill && (miss_victim == 1'b0);
    assign sec1_write_en = fill && (miss_victim == 1'b1);

    // ---------------- Core side ----------------
    assign i_valid = serve_hit || fill;
    assign i_rdata = fill ? bus_rdata
                   : (hit1 ? sec1_data : sec0_data);

    // ---------------- Bus side ----------------
    // Hold the accepted miss address until the SRAM response comes back.
    assign ic_addr  = miss_pending ? miss_addr : {i_addr[12:2], 2'b00};
    assign ic_wdata = 32'b0;
    assign ic_wstrb = 4'b0000;
endmodule
