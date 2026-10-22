`timescale 1ns / 1ps
//
// Data cache. Sits between a core's data port and the bus, like icache.
// 2-way set associative, 8 sets, one 32-bit word per line. Write-through,
// no allocate on a store miss, snoop-invalidate. dcache_FSM decides what each
// access does, dcache_snoop watches the bus, this module is the datapath.
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
// Victim: an invalid way if there is one, else the LRU way. Hits, store hits
// and fills update the LRU bit.
//
// flush clears every line. off (chicken bit) clears every line and makes every
// access pass through, so it may change at any time.
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
    assign index = d_addr[4:2];
    assign tag   = d_addr[10:5] & TAG_MASK;

    // Saved at the grant of a load miss, used in its FILL cycle.
    logic [12:0] miss_addr;
    logic [2:0]  miss_index;
    logic [5:0]  miss_tag;
    logic        miss_victim;

    logic        serve_hit, miss_start, store_hit, in_fill, fill, resp_bus, kill_fill;
    logic [2:0]  storage_index, snoop_index;
    assign storage_index = in_fill ? miss_index : index;

    // ---------------- Storage ----------------
    logic [31:0] sec0_data,  sec1_data;
    logic [5:0]  sec0_tag,   sec1_tag;
    logic        sec0_valid, sec1_valid;
    logic [5:0]  sec0_snoop_tag,   sec1_snoop_tag;
    logic        sec0_snoop_valid, sec1_snoop_valid;
    logic        sec0_inv, sec1_inv;
    logic        hit0, hit1, cache_hit;

    dcache_storage u_storage (
        .clk              (clk),
        .reset            (rst | flush | off),
        .index            (storage_index),
        .snoop_index      (snoop_index),

        .sec0_write_en    (fill && !miss_victim),
        .sec1_write_en    (fill &&  miss_victim),
        .write_data       (bus_rdata),
        .write_tag        (miss_tag),

        .sec0_update_en   (store_hit && hit0),
        .sec1_update_en   (store_hit && hit1),
        .update_data      (d_wdata),
        .update_strb      (d_wstrb),

        .sec0_inv         (sec0_inv),
        .sec1_inv         (sec1_inv),

        .sec0_data        (sec0_data),
        .sec1_data        (sec1_data),
        .sec0_tag         (sec0_tag),
        .sec1_tag         (sec1_tag),
        .sec0_valid       (sec0_valid),
        .sec1_valid       (sec1_valid),
        .sec0_snoop_tag   (sec0_snoop_tag),
        .sec1_snoop_tag   (sec1_snoop_tag),
        .sec0_snoop_valid (sec0_snoop_valid),
        .sec1_snoop_valid (sec1_snoop_valid)
    );

    // ---------------- Hit detection ----------------
    assign hit0      = sec0_valid && (sec0_tag == tag);
    assign hit1      = sec1_valid && (sec1_tag == tag);
    assign cache_hit = hit0 || hit1;

    // ---------------- FSM ----------------
    dcache_FSM u_fsm (
        .clk        (clk),
        .rst        (rst),
        .flush      (flush),
        .dcache_off (off),
        .d_req      (d_req),
        .d_mmio     (d_addr[12]),
        .d_store    (|d_wstrb),
        .d_gnt      (d_gnt),
        .cache_hit  (cache_hit),
        .dc_req     (dc_req),
        .dc_gnt     (dc_gnt),
        .kill_fill  (kill_fill),
        .serve_hit  (serve_hit),
        .miss_start (miss_start),
        .store_hit  (store_hit),
        .in_fill    (in_fill),
        .fill       (fill),
        .resp_bus   (resp_bus)
    );

    // ---------------- Snoop ----------------
    dcache_snoop #(.RAW(RAW), .OWN_SRC(int'(SRC))) u_snoop (
        .b_valid     (b_valid),
        .b_addr      (b_addr),
        .b_wstrb     (b_wstrb),
        .b_src       (b_src),
        .snoop_index (snoop_index),
        .sec0_valid  (sec0_snoop_valid),
        .sec0_tag    (sec0_snoop_tag),
        .sec1_valid  (sec1_snoop_valid),
        .sec1_tag    (sec1_snoop_tag),
        .inv0        (sec0_inv),
        .inv1        (sec1_inv),
        .in_fill     (in_fill),
        .miss_addr   (miss_addr),
        .kill_fill   (kill_fill)
    );

    // ---------------- LRU ----------------
    logic lru_victim, victim_way;
    assign victim_way = !sec0_valid ? 1'b0
                      : !sec1_valid ? 1'b1
                      : lru_victim;

    icache_lru u_lru (
        .clk        (clk),
        .rst        (rst),
        .index      (storage_index),
        .access     (serve_hit || store_hit || fill),
        .access_way (fill ? miss_victim : hit1),
        .victim_way (lru_victim)
    );

    // ---------------- Saved miss and hit data ----------------
    logic [31:0] hit_data;
    always_ff @(posedge clk) begin
        if (serve_hit)
            hit_data <= hit1 ? sec1_data : sec0_data;
        if (miss_start) begin
            miss_addr   <= d_addr;
            miss_index  <= index;
            miss_tag    <= tag;
            miss_victim <= victim_way;
        end
    end

    // ---------------- Core side ----------------
    assign d_rdata = resp_bus ? bus_rdata : hit_data;

    // ---------------- Bus side ----------------
    assign dc_addr  = d_addr;
    assign dc_wdata = d_wdata;
    assign dc_wstrb = d_wstrb;
endmodule
