`timescale 1ns / 1ps

// Two real cores, each connected to its own real instruction cache.
// Python provides independent memory responses to each pair.
// No shared arbiter or SRAM is instantiated here.

module dual_core_icache_harness (
    input logic clk,
    input logic rst,
    input logic flush,

    // ---------------- Pair 0: instruction memory ----------------
    output wire        ic0_req,
    output wire [12:0] ic0_addr,
    output wire [31:0] ic0_wdata,
    output wire [3:0]  ic0_wstrb,
    input  logic       ic0_gnt,
    input  logic [31:0] bus0_rdata,

    // Pair 0: data memory
    output wire        d0_req,
    output wire [12:0] d0_addr,
    output wire [31:0] d0_wdata,
    output wire [3:0]  d0_wstrb,
    input  logic       d0_gnt,
    input  logic [31:0] d0_rdata,

    // Pair 0: observation
    output wire        i0_req,
    output wire [12:0] i0_addr,
    output wire        i0_valid,
    output wire [31:0] i0_rdata,
    output wire        retire0,
    output wire [31:0] retire_pc0,

    // ---------------- Pair 1: instruction memory ----------------
    output wire        ic1_req,
    output wire [12:0] ic1_addr,
    output wire [31:0] ic1_wdata,
    output wire [3:0]  ic1_wstrb,
    input  logic       ic1_gnt,
    input  logic [31:0] bus1_rdata,

    // Pair 1: data memory
    output wire        d1_req,
    output wire [12:0] d1_addr,
    output wire [31:0] d1_wdata,
    output wire [3:0]  d1_wstrb,
    input  logic       d1_gnt,
    input  logic [31:0] d1_rdata,

    // Pair 1: observation
    output wire        i1_req,
    output wire [12:0] i1_addr,
    output wire        i1_valid,
    output wire [31:0] i1_rdata,
    output wire        retire1,
    output wire [31:0] retire_pc1
);

    // ---------------- Core-cache pair 0 ----------------
    core #(.NREGS(16)) u_core0 (
        .clk(clk),
        .rst(rst),

        .i_req(i0_req),
        .i_addr(i0_addr),
        .i_valid(i0_valid),
        .i_rdata(i0_rdata),

        .d_req(d0_req),
        .d_addr(d0_addr),
        .d_wdata(d0_wdata),
        .d_wstrb(d0_wstrb),
        .d_gnt(d0_gnt),
        .d_rdata(d0_rdata),

        .retire(retire0),
        .retire_pc(retire_pc0)
    );

    icache u_icache0 (
        .clk(clk),
        .rst(rst),
        .flush(flush),
        .off(1'b0),

        .i_req(i0_req),
        .i_addr(i0_addr),
        .i_valid(i0_valid),
        .i_rdata(i0_rdata),

        .ic_req(ic0_req),
        .ic_addr(ic0_addr),
        .ic_wdata(ic0_wdata),
        .ic_wstrb(ic0_wstrb),
        .ic_gnt(ic0_gnt),
        .bus_rdata(bus0_rdata)
    );

    // ---------------- Core-cache pair 1 ----------------
    core #(.NREGS(16)) u_core1 (
        .clk(clk),
        .rst(rst),

        .i_req(i1_req),
        .i_addr(i1_addr),
        .i_valid(i1_valid),
        .i_rdata(i1_rdata),

        .d_req(d1_req),
        .d_addr(d1_addr),
        .d_wdata(d1_wdata),
        .d_wstrb(d1_wstrb),
        .d_gnt(d1_gnt),
        .d_rdata(d1_rdata),

        .retire(retire1),
        .retire_pc(retire_pc1)
    );

    icache u_icache1 (
        .clk(clk),
        .rst(rst),
        .flush(flush),
        .off(1'b0),

        .i_req(i1_req),
        .i_addr(i1_addr),
        .i_valid(i1_valid),
        .i_rdata(i1_rdata),

        .ic_req(ic1_req),
        .ic_addr(ic1_addr),
        .ic_wdata(ic1_wdata),
        .ic_wstrb(ic1_wstrb),
        .ic_gnt(ic1_gnt),
        .bus_rdata(bus1_rdata)
    );

endmodule