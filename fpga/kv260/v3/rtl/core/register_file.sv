`timescale 1ns / 1ps
//
// Register file.
//
// Parameters
//   NREGS   16 = RV32E (x0..x15), 32 = RV32I (x0..x31).
//   BYPASS  0 = no write-first bypass. Required for multicycle and single-cycle
//           cores. 1 = bypass enabled, for pipelined cores only.
//
// WARNING: BYPASS must be 0 unless rd_data originates from a later pipeline
// stage. If rd_data is produced by the same instruction that is reading its
// operands, the bypass closes a combinational loop
// (rs1_data -> ALU -> rd_data -> rs1_data).
//
// Behaviour
//   Reads are combinational; the write commits on the rising edge of clk.
//   x0 reads as zero and is never written.
//   With NREGS = 16, x16..x31 read as zero and writes to them are dropped.
//   There is no reset: register contents are undefined until written.
//
module register_file #(
    parameter int NREGS  = 16,
    parameter bit BYPASS = 1'b0
) (
    input  logic        clk,
    input  logic [4:0]  rs1_addr,
    output logic [31:0] rs1_data,
    input  logic [4:0]  rs2_addr,
    output logic [31:0] rs2_data,
    input  logic        rd_we,
    input  logic [4:0]  rd_addr,
    input  logic [31:0] rd_data
);
    localparam int AW = $clog2(NREGS);

    logic [31:0] regs [0:NREGS-1];

    logic [AW-1:0] rs1_idx, rs2_idx, rd_idx;
    assign rs1_idx = rs1_addr[AW-1:0];
    assign rs2_idx = rs2_addr[AW-1:0];
    assign rd_idx  = rd_addr [AW-1:0];

    // Registers outside the implemented set behave as x0.
    logic rs1_bad, rs2_bad, rd_bad;
    assign rs1_bad = (NREGS < 32) & rs1_addr[4];
    assign rs2_bad = (NREGS < 32) & rs2_addr[4];
    assign rd_bad  = (NREGS < 32) & rd_addr [4];

`ifndef SYNTHESIS
    // Simulation only: keeps waveforms readable. Ignored by synthesis.
    initial for (int i = 0; i < NREGS; i++) regs[i] = 32'd0;
`endif

    always_ff @(posedge clk)
        if (rd_we && !rd_bad && (rd_idx != '0))
            regs[rd_idx] <= rd_data;

    assign rs1_data = (rs1_bad | (rs1_idx == '0))              ? 32'd0
                    : (BYPASS && rd_we && (rd_idx == rs1_idx)) ? rd_data
                    :                                            regs[rs1_idx];

    assign rs2_data = (rs2_bad | (rs2_idx == '0))              ? 32'd0
                    : (BYPASS && rd_we && (rd_idx == rs2_idx)) ? rd_data
                    :                                            regs[rs2_idx];
endmodule
