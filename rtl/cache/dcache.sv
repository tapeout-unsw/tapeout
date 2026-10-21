`timescale 1ns / 1ps
//
// Data cache. Sits between a core's data port and the bus, like icache.
// 2-way set associative, 8 sets, one 32-bit word per line. Write-through,
// no allocate on a store miss, kept coherent by snooping bus writes.
//
// Parameters
//   RAW   RAM byte address bits. Tag bits at or above RAW are zeroed, so
//         aliases of one RAM word share a line.
//   SRC   this port's b_src (1 = core 0, 2 = core 1); its own writes are not snooped.
//
// Core side: same timing as the arbiter port it replaces. d_gnt = accepted
// this cycle; load data is on d_rdata the cycle after.
//
// Address split: index = d_addr[4:2], tag = d_addr[10:5].
//
// Behaviour (cacheable = d_addr[12] == 0 and off == 0)
//   load hit     d_gnt at once, no bus request; next cycle d_rdata = cached word.
//   load miss    dc_req until dc_gnt; next cycle d_rdata = bus_rdata, which is
//                also written into the victim way.
//   store        always to the bus. On dc_gnt a hit merges the strobed bytes.
//   uncacheable  passed straight through.
//   Victim: an invalid way if there is one, else the LRU way. Hits, store
//   hits and fills update the LRU bit.
//
// Snoop: a RAM write by another source drops the matching line. If it hits
// the word being filled this cycle, the core still gets bus_rdata (its load
// was first on the bus) but the word is not installed.
//
// flush clears every line and the load in flight. off (chicken bit) clears
// every line and makes everything uncacheable.
//
module dcache #(
    parameter int         RAW = 10,
    parameter logic [2:0] SRC = 3'd1
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        flush,
    input  logic        off,

    input  logic        d_req,
    input  logic [12:0] d_addr,
    input  logic [31:0] d_wdata,
    input  logic [3:0]  d_wstrb,
    output logic        d_gnt,
    output logic [31:0] d_rdata,

    output logic        dc_req,
    output logic [12:0] dc_addr,
    output logic [31:0] dc_wdata,
    output logic [3:0]  dc_wstrb,
    input  logic        dc_gnt,
    input  logic [31:0] bus_rdata,

    input  logic        b_valid,
    input  logic [12:0] b_addr,
    input  logic [3:0]  b_wstrb,
    input  logic [2:0]  b_src
);
    localparam logic [5:0] TAG_MASK = 6'((1 << (RAW - 5)) - 1);

    // ---------------- Address split ----------------
    logic [2:0] index;
    logic [5:0] tag;
    logic       cacheable, is_load, is_store;
    assign index     = d_addr[4:2];
    assign tag       = d_addr[10:5] & TAG_MASK;
    assign cacheable = !d_addr[12] && !off;
    assign is_load   = d_req && (d_wstrb == 4'b0000);
    assign is_store  = d_req && (d_wstrb != 4'b0000);

    // Load accepted last cycle: a hit to return or a miss to install.
    logic       hit_q, fill_q, way_q;
    logic [2:0] index_q;
    logic [5:0] tag_q;
    logic [2:0] storage_index;
    assign storage_index = (hit_q || fill_q) ? index_q : index;

    // ---------------- Storage ----------------
    logic [31:0] sec0_data,  sec1_data;
    logic [5:0]  sec0_tag,   sec1_tag;
    logic        sec0_valid, sec1_valid;
    logic        install, store_hit, snoop_en;
    logic        hit0, hit1, hit;
    logic [2:0]  snoop_index;
    logic [5:0]  snoop_tag;

    dcache_storage u_storage (
        .clk            (clk),
        .reset          (rst | flush | off),
        .index          (storage_index),

        .sec0_write_en  (install && !way_q),
        .sec1_write_en  (install &&  way_q),
        .write_data     (bus_rdata),
        .write_tag      (tag_q),

        .sec0_update_en (store_hit && hit0),
        .sec1_update_en (store_hit && hit1),
        .update_data    (d_wdata),
        .update_strb    (d_wstrb),

        .snoop_en       (snoop_en),
        .snoop_index    (snoop_index),
        .snoop_tag      (snoop_tag),

        .sec0_data      (sec0_data),
        .sec1_data      (sec1_data),
        .sec0_tag       (sec0_tag),
        .sec1_tag       (sec1_tag),
        .sec0_valid     (sec0_valid),
        .sec1_valid     (sec1_valid)
    );

    // ---------------- Hit detection ----------------
    logic load_hit, fill_start;
    assign hit0       = sec0_valid && (sec0_tag == tag);
    assign hit1       = sec1_valid && (sec1_tag == tag);
    assign hit        = cacheable && (hit0 || hit1);
    assign load_hit   = is_load && hit;
    assign fill_start = is_load && cacheable && !hit && dc_gnt;
    assign store_hit  = is_store && hit && dc_gnt;

    // ---------------- Snoop ----------------
    assign snoop_en    = b_valid && (b_wstrb != 4'b0000) && !b_addr[12] && (b_src != SRC);
    assign snoop_index = b_addr[4:2];
    assign snoop_tag   = b_addr[10:5] & TAG_MASK;
    assign install     = fill_q && !off
                       && !(snoop_en && snoop_index == index_q && snoop_tag == tag_q);

    // ---------------- LRU ----------------
    logic lru_victim, victim_way;
    assign victim_way = !sec0_valid ? 1'b0
                      : !sec1_valid ? 1'b1
                      : lru_victim;

    icache_lru u_lru (
        .clk        (clk),
        .rst        (rst),
        .index      (storage_index),
        .access     (load_hit || store_hit || install),
        .access_way (install ? way_q : hit1),
        .victim_way (lru_victim)
    );

    // ---------------- Load in flight ----------------
    always_ff @(posedge clk) begin
        if (rst || flush) begin
            hit_q  <= 1'b0;
            fill_q <= 1'b0;
        end else begin
            hit_q  <= load_hit;
            fill_q <= fill_start;
        end
        if (load_hit || fill_start) begin
            index_q <= index;
            tag_q   <= tag;
            way_q   <= load_hit ? hit1 : victim_way;
        end
    end

    // ---------------- Core side ----------------
    assign d_gnt   = load_hit || dc_gnt;
    assign d_rdata = hit_q ? (way_q ? sec1_data : sec0_data) : bus_rdata;

    // ---------------- Bus side ----------------
    assign dc_req   = d_req && !load_hit;
    assign dc_addr  = d_addr;
    assign dc_wdata = d_wdata;
    assign dc_wstrb = d_wstrb;
endmodule
