`timescale 1ns / 1ps

/*
There are five things that can request to read/write memory, which are the
Bootloader, core 0 data port, core 1 data port, core 0 I-cache and core 1 I-cache

Each cycle, the arbiter grants only one request and forwards the granted request to the address decoder.

Each request is formatted as x_req, x_addr (13), x_wdata (32), x_wstrb (4).
x_wstrb = 0000 is a read, anything else a write.

When loading = 1, we are in the bootloader stage, so we grant boot automatically whenever boot_req is high

When loading = 0, we use round-robin to choose between sources using a 2 bit pointer.
(0 = d0, 1 = d1, 2 = ic0, 3 = ic1)
We check the source at the pointer to see if it is requesting, if it isn't, the next one is checked,
and so on, this is done in parallel. After a grant, the pointer goes to the one after the winner.
*/

module arbiter (
    input  logic        clk,
    input  logic        rst,
    input  logic        loading,    // Whether or not we are in the bootloader stage

    // Bootloader request
    input  logic        boot_req,
    input  logic [12:0] boot_addr,
    input  logic [31:0] boot_wdata,
    input  logic [3:0]  boot_wstrb,

    // Core 0 data port request
    input  logic        d0_req,
    input  logic [12:0] d0_addr,
    input  logic [31:0] d0_wdata,
    input  logic [3:0]  d0_wstrb,
    output logic        d0_gnt,

    // Core 1 data port request
    input  logic        d1_req,
    input  logic [12:0] d1_addr,
    input  logic [31:0] d1_wdata,
    input  logic [3:0]  d1_wstrb,
    output logic        d1_gnt,

    // Core 0 instruction cache request
    input  logic        ic0_req,
    input  logic [12:0] ic0_addr,
    input  logic [31:0] ic0_wdata,
    input  logic [3:0]  ic0_wstrb,
    output logic        ic0_gnt,

    // Core 1 instruction cache request
    input  logic        ic1_req,
    input  logic [12:0] ic1_addr,
    input  logic [31:0] ic1_wdata,
    input  logic [3:0]  ic1_wstrb,
    output logic        ic1_gnt,

    // Arbiter to bus
    output logic        b_valid,    // High when a request won
    output logic [12:0] b_addr,     // Winner's address
    output logic [31:0] b_wdata,    // Winner's write data
    output logic [3:0]  b_wstrb,    // Winner's write strobe
    output logic [2:0]  b_src       // Winner's id (0 = boot, 1 = d0, 2 = d1, 3 = ic0, 4 = ic1)
);

    logic [2:0] b_src_temp;
    logic [1:0] rr_pointer;         // Round-robin pointer (0 = d0, 1 = d1, 2 = ic0, 3 = ic1)

    always_comb begin
        b_src_temp = 3'd5;          // Default to nobody won (represented as 5)
        case (rr_pointer)
            2'd0: begin
                if (d0_req) b_src_temp = 3'd1;
                else if (d1_req) b_src_temp = 3'd2;
                else if (ic0_req) b_src_temp = 3'd3;
                else if (ic1_req) b_src_temp = 3'd4;
            end
        endcase

        // Each source's grant output depends on b_src
        d0_gnt = (b_src == 3'd1);
        d1_gnt = (b_src == 3'd2);
        ic0_gnt = (b_src == 3'd3);
        ic1_gnt = (b_src == 3'd4);

        // If there is a boot request or we are in the loading stage, then winner must be bootloader
        b_src = (boot_req || loading) ? 3'b0 : b_src_temp;
    end

    always_ff @ (posedge clk) begin
        if (rst) begin
            rr_pointer <= 2'b0;
        end else begin
            // Increment rr_pointer based on previous winner
            if 
        end
    end
endmodule
