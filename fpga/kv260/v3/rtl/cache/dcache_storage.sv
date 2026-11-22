`timescale 1ns / 1ps
//
// Data cache storage, same layout as icache_storage: two sections (ways) of
// 8 lines, each {valid, 6-bit tag, 32-bit data}.
//
// index        read port and write port: data, tag and valid of both sections
// snoop_index  second read port for dcache_snoop: tag and valid of both sections
//
// write   fill a line: data, tag, valid = 1
// update  store hit: merge update_data into the line under update_strb
// inv     clear valid at snoop_index. A write to the same line in the same
//         cycle wins.
// reset   clears the valid bits only
//
module dcache_storage (
    input  logic        clk,
    input  logic        reset,

    input  logic [2:0]  index,
    input  logic [2:0]  snoop_index,

    input  logic        sec0_write_en,
    input  logic        sec1_write_en,
    input  logic [31:0] write_data,
    input  logic [5:0]  write_tag,

    input  logic        sec0_update_en,
    input  logic        sec1_update_en,
    input  logic [31:0] update_data,
    input  logic [3:0]  update_strb,

    input  logic        sec0_inv,
    input  logic        sec1_inv,

    output logic [31:0] sec0_data,
    output logic [31:0] sec1_data,
    output logic [5:0]  sec0_tag,
    output logic [5:0]  sec1_tag,
    output logic        sec0_valid,
    output logic        sec1_valid,

    output logic [5:0]  sec0_snoop_tag,
    output logic [5:0]  sec1_snoop_tag,
    output logic        sec0_snoop_valid,
    output logic        sec1_snoop_valid
);
    logic [31:0] sec0_memory [0:7];
    logic [31:0] sec1_memory [0:7];

    logic [5:0] sec0_tags [0:7];
    logic [5:0] sec1_tags [0:7];

    logic sec0_valid_bits [0:7];
    logic sec1_valid_bits [0:7];

    assign sec0_data  = sec0_memory[index];
    assign sec1_data  = sec1_memory[index];
    assign sec0_tag   = sec0_tags[index];
    assign sec1_tag   = sec1_tags[index];
    assign sec0_valid = sec0_valid_bits[index];
    assign sec1_valid = sec1_valid_bits[index];

    assign sec0_snoop_tag   = sec0_tags[snoop_index];
    assign sec1_snoop_tag   = sec1_tags[snoop_index];
    assign sec0_snoop_valid = sec0_valid_bits[snoop_index];
    assign sec1_snoop_valid = sec1_valid_bits[snoop_index];

    always_ff @(posedge clk) begin
        if (reset) begin
            for (int i = 0; i < 8; i++) begin
                sec0_valid_bits[i] <= 1'b0;
                sec1_valid_bits[i] <= 1'b0;
            end
        end else begin
            if (sec0_inv)      sec0_valid_bits[snoop_index] <= 1'b0;
            if (sec1_inv)      sec1_valid_bits[snoop_index] <= 1'b0;
            if (sec0_write_en) sec0_valid_bits[index] <= 1'b1;
            if (sec1_write_en) sec1_valid_bits[index] <= 1'b1;
        end
    end

    always_ff @(posedge clk) begin
        if (sec0_write_en) begin
            sec0_memory[index] <= write_data;
            sec0_tags[index]   <= write_tag;
        end else if (sec0_update_en) begin
            for (int b = 0; b < 4; b++)
                if (update_strb[b]) sec0_memory[index][8*b +: 8] <= update_data[8*b +: 8];
        end

        if (sec1_write_en) begin
            sec1_memory[index] <= write_data;
            sec1_tags[index]   <= write_tag;
        end else if (sec1_update_en) begin
            for (int b = 0; b < 4; b++)
                if (update_strb[b]) sec1_memory[index][8*b +: 8] <= update_data[8*b +: 8];
        end
    end
endmodule
