`timescale 1ns / 1ps


module imm_gen(
    input logic [31:0] inst, //instruction to decode
    input logic [2:0] ctrl,
    output logic [31:0] imm
    );

    //5 types
    localparam logic [2:0] IMM_I = 3'd0;
    localparam logic [2:0] IMM_S = 3'd1;
    localparam logic [2:0] IMM_B = 3'd2;
    localparam logic [2:0] IMM_U = 3'd3;
    localparam logic [2:0] IMM_J = 3'd4;
    
    always_comb begin
    case (ctrl)
        //assembling bits from scrambled input
        IMM_I: imm = {{20{inst[31]}}, inst[31:20]};
        IMM_B: imm = {{19{inst[31]}}, inst[31], inst[7], inst[30:25], inst[11:8], 1'b0};
        IMM_S: imm = {{20{inst[31]}}, inst[31:25], inst[11:7]};
        IMM_U: imm = {inst[31:12], 12'b0};
        IMM_J: imm = {{11{inst[31]}}, inst[31], inst[19:12], inst[20], inst[30:21], 1'b0};
    
    default: imm = '0;
    endcase
end

endmodule