`timescale 1ns / 1ps
//
// Test harness, not RTL: the shared bus for ONE core and its I-cache.
//
//   d0  (core 0 data port, as core.sv drives it in S_MEM) ─┐
//                                                           ├─ arbiter ─ SRAM side of ─ sram_mem
//   ic0 (core 0 I-cache miss port, reads only)        ──────┘            addr_decode
//
// The arbiter is the real one. The SRAM side of addr_decode is written out
// inline because addr_decode on main is still a stub; once it is implemented,
// replace that block with an addr_decode instance so this test covers it too.
// The other requesters (bootloader, core 1) are tied off. There is no MMIO
// model: the test only reads SRAM addresses.
//
module sram_bus_harness #(
    parameter int RAW = 11                     // RAM byte-address bits: 10 = 1 KiB, 11 = 2 KiB
) (
    input  logic        clk,
    input  logic        rst,

    input  logic        d0_req,
    input  logic [12:0] d0_addr,
    input  logic [31:0] d0_wdata,
    input  logic [3:0]  d0_wstrb,
    output logic        d0_gnt,

    input  logic        ic0_req,
    input  logic [12:0] ic0_addr,
    output logic        ic0_gnt,

    output logic [31:0] bus_rdata,             // what every requester sees
    output logic [2:0]  data_src               // whose read bus_rdata holds: last cycle's b_src
);
    logic        b_valid;
    logic [12:0] b_addr;
    logic [31:0] b_wdata;
    logic [3:0]  b_wstrb;
    logic [2:0]  b_src;

    arbiter u_arbiter (
        .clk(clk), .rst(rst), .loading(1'b0),
        .boot_req(1'b0), .boot_addr(13'd0), .boot_wdata(32'd0), .boot_wstrb(4'd0),
        .d0_req(d0_req),   .d0_addr(d0_addr),   .d0_wdata(d0_wdata), .d0_wstrb(d0_wstrb), .d0_gnt(d0_gnt),
        .d1_req(1'b0),     .d1_addr(13'd0),     .d1_wdata(32'd0),    .d1_wstrb(4'd0),     .d1_gnt(),
        .ic0_req(ic0_req), .ic0_addr(ic0_addr), .ic0_wdata(32'd0),   .ic0_wstrb(4'd0),    .ic0_gnt(ic0_gnt),
        .ic1_req(1'b0),    .ic1_addr(13'd0),    .ic1_wdata(32'd0),   .ic1_wstrb(4'd0),    .ic1_gnt(),
        .b_valid(b_valid), .b_addr(b_addr), .b_wdata(b_wdata), .b_wstrb(b_wstrb), .b_src(b_src)
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
