module icache_storage (
    input logic clk,
    input logic reset,

    // Index Bits
    input logic[2:0] index,

    // Section 0
    input logic sec0_write_en,
    input logic [31:0] sec0_write_data,
    // 4 Bit Version
    input logic [3:0] sec0_write_tag,
    // 6 Bit Version
    // input logic [5:0] sec0_write_tag,

    // Section 1
    input logic sec1_write_en,
    input logic [31:0] sec1_write_data,
    // 4 Bit
    input logic [3:0] sec1_write_tag,
    // 6 Bit
    // input logic [5:0] sec1_write_tag,

    // Reading
    output logic [31:0] sec0_data,
    output logic [31:0] sec1_data,

    // Tags (4)
    output logic [3:0] sec0_tag,
    output logic [3:0] sec1_tag,

    /* Tags (6)
    output logic [5:0] sec0_tag,
    output logic [5:0] sec1_tag,
    */

    // Valid Bit
    output logic sec0_valid,
    output logic sec1_valid
);

// Cache Storage Bits
    logic [31:0] sec0_memory [0:7];
    logic [31:0] sec1_memory [0:7];

    logic [3:0] sec0_tags [0:7];
    logic [3:0] sec1_tags [0:7];

    /* Tags (6)
    logic [5:0] sec0_tags [0:7];
    logic [5:0] sec1_tags [0:7];
    */

    logic sec0_valid_bits [0:7];
    logic sec1_valid_bits [0:7];

// Cache Read
    assign sec0_data = sec0_memory[index];
    assign sec1_data = sec1_memory[index];

    assign sec0_tag = sec0_tags[index];
    assign sec1_tag = sec1_tags[index];

    assign sec0_valid = sec0_valid_bits[index];
    assign sec1_valid = sec1_valid_bits[index];


always_ff @(posedge clk) begin

    // Cache Resetting
    if (reset) begin
        for (int i = 0; i < 8; i++) begin

            sec0_memory[i] <= 32'b0;
            sec1_memory[i] <= 32'b0;

            sec0_tags[i] <= 4'b0;
            sec1_tags[i] <= 4'b0;

            sec0_valid_bits[i] <= 1'b0;
            sec1_valid_bits[i] <= 1'b0;
        end
    end

    // Cache Writing
    else begin
        if (sec0_write_en) begin
            sec0_memory[index] <= sec0_write_data;
            sec0_tags[index] <= sec0_write_tag;
            sec0_valid_bits[index] <= 1'b1;
        end

        if (sec1_write_en) begin
            sec1_memory[index] <= sec1_write_data;
            sec1_tags[index] <= sec1_write_tag;
            sec1_valid_bits[index] <= 1'b1;
        end
    end
end
endmodule