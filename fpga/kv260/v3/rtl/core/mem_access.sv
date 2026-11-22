`timescale 1ns / 1ps
// Byte/halfword memory access support.
//
// RV32I loads and stores come in three widths, selected by func3:
//   loads : 000 lb   001 lh   010 lw   100 lbu  101 lhu
//   stores: 000 sb   001 sh   010 sw
//
// Data memory is word-addressed, so a sub-word access must select the right
// lane using the low two address bits, and a load must sign- or zero-extend the
// extracted value back to 32 bits.
module mem_access (
    input  logic [2:0]  func3,
    input  logic [1:0]  addr_lo,     // addr[1:0] - which byte within the word
    input  logic        mem_write,
    // store path
    input  logic [31:0] store_data,  // raw rs2 value
    output logic [31:0] w_data,      // value positioned into the correct lane
    output logic [3:0]  w_strb,      // which bytes to actually write
    // load path
    input  logic [31:0] raw_rdata,   // full word read from memory
    output logic [31:0] load_data    // extracted and extended
);
    //-------------------------------------------------------------------
    // Store: replicate the value into every lane, then use the strobe to
    // pick which lanes are actually written. Cheaper than shifting.
    //-------------------------------------------------------------------
    always_comb begin
        w_data = store_data;
        w_strb = 4'b0000;
        if (mem_write) begin
            case (func3)
                3'b000: begin                          // sb
                    w_data = {4{store_data[7:0]}};
                    w_strb = 4'b0001 << addr_lo;
                end
                3'b001: begin                          // sh
                    w_data = {2{store_data[15:0]}};
                    w_strb = addr_lo[1] ? 4'b1100 : 4'b0011;
                end
                default: begin                         // sw
                    w_data = store_data;
                    w_strb = 4'b1111;
                end
            endcase
        end
    end

    //-------------------------------------------------------------------
    // Load: select the lane, then extend.
    //-------------------------------------------------------------------
    logic [7:0]  byte_sel;
    logic [15:0] half_sel;

    always_comb begin
        case (addr_lo)
            2'b00: byte_sel = raw_rdata[7:0];
            2'b01: byte_sel = raw_rdata[15:8];
            2'b10: byte_sel = raw_rdata[23:16];
            2'b11: byte_sel = raw_rdata[31:24];
        endcase
        half_sel = addr_lo[1] ? raw_rdata[31:16] : raw_rdata[15:0];

        case (func3)
            3'b000:  load_data = {{24{byte_sel[7]}},  byte_sel};   // lb  signed
            3'b001:  load_data = {{16{half_sel[15]}}, half_sel};   // lh  signed
            3'b100:  load_data = {24'd0, byte_sel};                // lbu zero
            3'b101:  load_data = {16'd0, half_sel};                // lhu zero
            default: load_data = raw_rdata;                        // lw
        endcase
    end
endmodule
