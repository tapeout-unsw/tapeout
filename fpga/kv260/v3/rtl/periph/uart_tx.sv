`timescale 1ns / 1ps

//Used for uart output
//during printing, the core keeps exectuting until wait is done, pipeline keeps changing

module uart_tx #(
    parameter int CLK_FREQ  = 100_000_000,
    parameter int BAUD_RATE = 115_200
) (
    input  logic       clk,
    input  logic       rst,
    input  logic       tx_start,
    input  logic [7:0] tx_data, //8 bits, 10 bit times for full transmission
    output logic       tx,
    output logic       tx_busy
    
    
);

localparam int CYCLES_PER_BIT = CLK_FREQ / BAUD_RATE;

    // state machine
    typedef enum logic [1:0] {IDLE, START, DATA, STOP} state_t;
    state_t state;

    // internal state
    logic [15:0] cycle_cnt;    // counts to CYCLES_PER_BIT-1 (868 needs 10 bits; 16 is roomy)
    logic [2:0]  bit_idx;      // which data bit, 0-7
    logic [7:0]  shift_reg;    // latched copy of tx_data

    assign tx_busy = (state != IDLE); //busy when not idle

    always_ff @(posedge clk) begin
        if (rst) begin
            state     <= IDLE;
            tx        <= 1'b1;   //active high
            cycle_cnt <= '0;
            bit_idx   <= '0;
            shift_reg <= '0;
        end else begin
            case (state)
                IDLE: begin
                    tx        <= 1'b1;
                    if(tx_start) begin
                        shift_reg <= tx_data; //latch shift register with the data, because pipeline will change
                        cycle_cnt <= '0;
                        bit_idx   <= '0;
                        state     <= START; //advance
                    end
                end
                START: begin
                    tx <= 1'b0;
                    if(32'(cycle_cnt) == CYCLES_PER_BIT -1) begin
                        cycle_cnt <= 0;
                        state <= DATA;
                    end else begin
                        cycle_cnt <= cycle_cnt + 1;
                    end
                end
                DATA: begin
                    tx <= shift_reg[bit_idx];        // always drive the current bit
                    if (32'(cycle_cnt) == CYCLES_PER_BIT - 1) begin
                        cycle_cnt <= '0;
                        if (bit_idx == 3'd7)        //all bits sent
                            state <= STOP;
                        else
                            bit_idx <= bit_idx + 1; //advance to next bit
                    end else begin
                        cycle_cnt <= cycle_cnt + 1; 
                    end 
                end
                STOP: begin
                    tx <= 1'b1;
                    if(32'(cycle_cnt) == CYCLES_PER_BIT -1) begin
                        cycle_cnt <= 0;
                        state <= IDLE;
                    end else begin
                        cycle_cnt <= cycle_cnt + 1;
                    end
                end
            endcase
        end
    end

endmodule