`timescale 1ns / 1ps
//
// Test harness for Both Cores to their ICache
// This is NOT a RTL Connection
// This test purely tests between 1 core and its icache
//
// Python supplies instruction-memory responses and data-port grants.
// Python observes fetches, completed instructions, and result stores.
//

module core_icache_harness (
    input logic clk,
    input logic rst,
    input logic flush,

    // Cache memory
    output wire        ic_req,
    output wire [12:0] ic_addr,
    output wire [31:0] ic_wdata,
    output wire [3:0]  ic_wstrb,
    input logic        ic_gnt,
    input logic [31:0] bus_rdata,

    // Core memory
    output wire        d_req,
    output wire [12:0] d_addr,
    output wire [31:0] d_wdata,
    output wire [3:0]  d_wstrb,
    input logic        d_gnt,
    input logic [31:0] d_rdata,

    // observation
    output wire        i_req,
    output wire [12:0] i_addr,
    output wire        i_valid,
    output wire [31:0] i_rdata,
    output wire        retire,
    output wire [31:0] retire_pc
);

 core u_core (
        .clk(clk), .rst(rst), .i_req(i_req), .i_addr(i_addr),
        .i_valid(i_valid), .i_rdata(i_rdata), .d_req(d_req), .d_addr(d_addr),
        .d_wdata(d_wdata), .d_wstrb(d_wstrb), .d_gnt(d_gnt), .d_rdata(d_rdata),
        .retire(retire), .retire_pc(retire_pc)
    );

    icache u_cache (
        .clk(clk), .rst(rst), .flush(flush), .i_req(i_req),
        .i_addr(i_addr), .i_valid(i_valid), .i_rdata(i_rdata),
        .ic_req(ic_req), .ic_addr(ic_addr), .ic_wdata(ic_wdata),
        .ic_wstrb(ic_wstrb), .ic_gnt(ic_gnt), .bus_rdata(bus_rdata)
    );

endmodule
