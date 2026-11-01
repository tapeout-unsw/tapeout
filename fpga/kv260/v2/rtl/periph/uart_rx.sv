`timescale 1ns / 1ps
// UART receiver. Mirrors uart_tx but must *find* the timing rather than set it:
// it detects the falling start-bit edge, waits 1.5 bit-times to land in the middle
// of data bit 0, then samples every bit-time. Mid-bit sampling gives maximum
// tolerance to clock drift between the two ends.
module uart_rx #(
    parameter int CLK_FREQ  = 100_000_000,
    parameter int BAUD_RATE = 115_200
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       rx,          // serial input pin
    output logic [7:0] rx_data,     // received byte
    output logic       rx_valid     // one-cycle pulse when rx_data is fresh
);
    localparam int CYCLES_PER_BIT = CLK_FREQ / BAUD_RATE;
    localparam int HALF_BIT       = CYCLES_PER_BIT / 2;

    typedef enum logic [1:0] {IDLE, START, DATA, STOP} state_t;
    state_t state;

    logic [15:0] cycle_cnt;
    logic [2:0]  bit_idx;
    logic [7:0]  shift_reg;

    // Two-stage synchroniser: rx arrives from outside and is asynchronous to clk.
    // Sampling it directly risks metastability.
    logic rx_sync1, rx_sync2;
    always_ff @(posedge clk) begin
        rx_sync1 <= rx;
        rx_sync2 <= rx_sync1;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state     <= IDLE;
            cycle_cnt <= '0;
            bit_idx   <= '0;
            shift_reg <= '0;
            rx_data   <= '0;
            rx_valid  <= 1'b0;
        end else begin
            rx_valid <= 1'b0;                 // default: single-cycle pulse
            case (state)
                IDLE: begin
                    if (!rx_sync2) begin      // falling edge = start bit
                        cycle_cnt <= '0;
                        state     <= START;
                    end
                end
                START: begin
                    // wait half a bit-time, then confirm we're still low
                    if (32'(cycle_cnt) == HALF_BIT - 1) begin
                        if (!rx_sync2) begin  // genuine start bit, not a glitch
                            cycle_cnt <= '0;
                            bit_idx   <= '0;
                            state     <= DATA;
                        end else
                            state <= IDLE;    // false start, abort
                    end else
                        cycle_cnt <= cycle_cnt + 1;
                end
                DATA: begin
                    if (32'(cycle_cnt) == CYCLES_PER_BIT - 1) begin
                        cycle_cnt          <= '0;
                        shift_reg[bit_idx] <= rx_sync2;   // sample mid-bit, LSB first
                        if (bit_idx == 3'd7)
                            state <= STOP;
                        else
                            bit_idx <= bit_idx + 1;
                    end else
                        cycle_cnt <= cycle_cnt + 1;
                end
                STOP: begin
                    if (32'(cycle_cnt) == CYCLES_PER_BIT - 1) begin
                        cycle_cnt <= '0;
                        rx_data   <= shift_reg;
                        rx_valid  <= 1'b1;    // byte complete
                        state     <= IDLE;
                    end else
                        cycle_cnt <= cycle_cnt + 1;
                end
            endcase
        end
    end
endmodule
