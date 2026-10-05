module icache_storage {
    input logic clk,
    input logic reset,

    // Index Bits
    input logic[2:0] index,

    // Section 0
    input logic section0_write_en,
    input logic [31:0] section0_write_data,
    input logic [3:0] section0_write_tag,

    // Section 1
    input logic section1_write_en,
    input logic [31:0] section1_write_data,
    input logic [3:0] section1_write_tag,

    // Reading
    output logic [31:0] sec0_data,
    output logic [31:0] sec1_data,

    // Tags
    output logic [3:0] sec0_tag,
    output logic [3:0] sec1_tag,

    // Valid Bit
    output logic sec0_valid,
    output logic sec1_valid,
}

// Cache Storage