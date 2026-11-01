`timescale 1ns / 1ps
// Hardware bootloader: receives a program over UART, writes it into the shared
// memory one word per bus write, then releases the cores.
//
// Protocol (host -> board), little-endian:
//   1. 4 bytes : bits 27:0 are the count and bits 31:28 are the chicken bits
//   2. N*4 bytes : the memory image, one 32-bit word per 4 bytes, written
//                  from address 0
//
// The bootloader keeps listening after a program has been loaded. Four more
// bytes arriving are interpreted as a new word count, which drops core_run
// (resetting the cores) and starts a fresh load. That makes it possible to run
// a suite of test programs back to back without touching the reset button.
// 
// Chicken bit control (4 bits):
// 1 = off: [3] ic0 [2] ic1 [1] dc0 [0] dc1
module bootloader (
    input  logic        clk,
    input  logic        rst,
    input  logic [7:0]  rx_data,
    input  logic        rx_valid,
    output logic        boot_req,       // one-cycle write pulse per word
    output logic [12:0] boot_addr,      // byte address, word aligned
    output logic [31:0] boot_wdata,
    output logic        core_run,       // high once loading is complete
    output logic        loading,        // high while receiving
    output logic [3:0]  chicken         // Control for cache chicken bits
);
    typedef enum logic [1:0] {GET_LEN, GET_PROG, DONE} state_t;
    state_t state;

    logic [1:0]  byte_idx;
    logic [31:0] word_buf;
    logic [31:0] word_count;
    logic [31:0] words_got;

    // the word being assembled, completed by the byte arriving this cycle
    logic [31:0] full_word;
    assign full_word = {rx_data, word_buf[31:8]};

    // The registered write strobe commits on the following clock edge. Keep
    // ownership of memory and hold the cores in reset through the final write.
    assign loading  = (state != DONE) || boot_req;
    assign core_run = (state == DONE) && !boot_req;

    always_ff @(posedge clk) begin
        if (rst) begin
            state      <= GET_LEN;
            byte_idx   <= '0;
            word_buf   <= '0;
            word_count <= '0;
            words_got  <= '0;
            boot_req    <= 1'b0;
            boot_addr <= '0;
            boot_wdata <= '0;
            chicken <= 4'b0;
        end else begin
            boot_req <= 1'b0;

            if (rx_valid) begin
                word_buf <= full_word;

                if (byte_idx == 2'd3) begin
                    byte_idx <= '0;
                    case (state)
                        GET_LEN: begin
                            word_count <= {4'b0, full_word[27:0]};  // Ignore chicken bits
                            words_got  <= '0;
                            state      <= GET_PROG;
                            chicken <= full_word[31:28];    // Top 4 bits of full word are chicken bits
                        end
                        GET_PROG: begin
                            boot_req    <= 1'b1;
                            boot_addr <= 13'(words_got << 2);
                            boot_wdata <= full_word;
                            words_got  <= words_got + 1;
                            if (words_got + 1 == word_count)
                                state <= DONE;
                        end
                        DONE: begin
                            // a new word count: reload, which drops core_run
                            word_count <= {4'b0, full_word[27:0]};
                            words_got  <= '0;
                            state      <= GET_PROG;
                            chicken <= full_word[31:28];
                        end
                        default: ;
                    endcase
                end else
                    byte_idx <= byte_idx + 1;
            end
        end
    end
endmodule