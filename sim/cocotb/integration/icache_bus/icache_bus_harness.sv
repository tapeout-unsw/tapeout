`timescale 1ns / 1ps
//
// Test harness, not RTL: both cores' I-caches on the shared bus.
//
//   core 0 (test) ─ icache 0 ─┐
//   core 1 (test) ─ icache 1 ─┤
//   d0 (test) ────────────────┼─ arbiter ─ SRAM side of ─ sram_mem
//   bootloader (test) ────────┘            addr_decode
//
// The caches, the arbiter and the SRAM are the real RTL. As in
// sram_bus_harness.sv, the SRAM side of addr_decode is written out inline
// because addr_decode on main is still a stub; once it is implemented,
// replace that block with an addr_decode instance. Core 1's data port is tied
// off and there is no MMIO model: the test only reads SRAM addresses.
// flush goes to both caches, as soc_top drives it.
//
module icache_bus_harness #(
    parameter int RAW = 11                     // RAM byte-address bits: 10 = 1 KiB, 11 = 2 KiB
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        loading,               // bootloader owns the bus
    input  logic        flush,

    // core 0 and core 1 instruction ports
    input  logic        i0_req,
    input  logic [12:0] i0_addr,
    output logic        i0_valid,
    output logic [31:0] i0_rdata,
    input  logic        i1_req,
    input  logic [12:0] i1_addr,
    output logic        i1_valid,
    output logic [31:0] i1_rdata,

    // core 0 data port, used to write programs and to add traffic
    input  logic        d0_req,
    input  logic [12:0] d0_addr,
    input  logic [31:0] d0_wdata,
    input  logic [3:0]  d0_wstrb,
    output logic        d0_gnt,

    // bootloader write port (soc_top ties boot_wstrb to 1111)
    input  logic        boot_req,
    input  logic [12:0] boot_addr,
    input  logic [31:0] boot_wdata,

    // observation
    output logic        ic0_req,
    output logic        ic0_gnt,
    output logic        ic1_req,
    output logic        ic1_gnt,
    output logic [31:0] bus_rdata,             // what every requester sees
    output logic [2:0]  data_src               // whose read bus_rdata holds: last cycle's b_src
);
    // ---------------- the two caches ----------------
    logic [12:0] ic0_addr, ic1_addr;
    logic [31:0] ic0_wdata, ic1_wdata;
    logic [3:0]  ic0_wstrb, ic1_wstrb;

    icache #(.RAW(RAW)) u_icache0 (
        .clk(clk), .rst(rst), .flush(flush),
        .i_req(i0_req), .i_addr(i0_addr), .i_valid(i0_valid), .i_rdata(i0_rdata),
        .ic_req(ic0_req), .ic_addr(ic0_addr), .ic_wdata(ic0_wdata), .ic_wstrb(ic0_wstrb),
        .ic_gnt(ic0_gnt), .bus_rdata(bus_rdata)
    );

    icache #(.RAW(RAW)) u_icache1 (
        .clk(clk), .rst(rst), .flush(flush),
        .i_req(i1_req), .i_addr(i1_addr), .i_valid(i1_valid), .i_rdata(i1_rdata),
        .ic_req(ic1_req), .ic_addr(ic1_addr), .ic_wdata(ic1_wdata), .ic_wstrb(ic1_wstrb),
        .ic_gnt(ic1_gnt), .bus_rdata(bus_rdata)
    );

    // ---------------- arbiter ----------------
    logic        b_valid;
    logic [12:0] b_addr;
    logic [31:0] b_wdata;
    logic [3:0]  b_wstrb;
    logic [2:0]  b_src;

    arbiter u_arbiter (
        .clk(clk), .rst(rst), .loading(loading),
        .boot_req(boot_req), .boot_addr(boot_addr), .boot_wdata(boot_wdata), .boot_wstrb(4'b1111),
        .d0_req(d0_req),   .d0_addr(d0_addr),   .d0_wdata(d0_wdata),  .d0_wstrb(d0_wstrb),  .d0_gnt(d0_gnt),
        .d1_req(1'b0),     .d1_addr(13'd0),     .d1_wdata(32'd0),     .d1_wstrb(4'd0),      .d1_gnt(),
        .ic0_req(ic0_req), .ic0_addr(ic0_addr), .ic0_wdata(ic0_wdata), .ic0_wstrb(ic0_wstrb), .ic0_gnt(ic0_gnt),
        .ic1_req(ic1_req), .ic1_addr(ic1_addr), .ic1_wdata(ic1_wdata), .ic1_wstrb(ic1_wstrb), .ic1_gnt(ic1_gnt),
        .b_valid(b_valid), .b_addr(b_addr), .b_wdata(b_wdata), .b_wstrb(b_wstrb), .b_src(b_src),
        .d0_atomic(1'b0), .d1_atomic(1'b0)      // no atomics in this harness
    );

    // ---------------- SRAM side of addr_decode (see the header) ----------------
    logic           s_en;
    logic [RAW-3:0] s_addr;
    logic [31:0]    s_rdata;

    assign s_en   = b_valid & ~b_addr[12];
    assign s_addr = b_addr[RAW-1:2];

    sram_mem #(.WORDS(1 << (RAW - 2))) u_sram (
        .clk  (clk),
        .en   (s_en),
        .addr (s_addr),
        .wdata(b_wdata),
        .wstrb(b_wstrb),
        .rdata(s_rdata)
    );

    assign bus_rdata = s_rdata;

    always_ff @(posedge clk)
        data_src <= b_valid ? b_src : 3'd5;    // 5 = nobody was granted
endmodule
