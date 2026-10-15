`timescale 1ns / 1ps
//
// Instruction cache FSM
//
// IDLE - check for a hit or miss
// MISS - request data from the bus and wait for a grant
// FILL - the SRAM data is ready, so write it into the cache
//
// The SRAM read takes one cycle. When ic_gnt goes high, the SRAM gets the
// address and the FSM moves to FILL on the same clock edge. bus_rdata is then
// ready during the FILL cycle.
//
// TEST
module icache_FSM (
    input  logic clk,
    input  logic rst,
    input  logic flush,

    // Result from checking both ways
    input  logic i_req,
    input  logic cache_hit,

    // Grant from the arbiter
    input  logic ic_gnt,

    // Signals used by icache.sv
    output logic serve_hit,
    output logic miss_start,
    output logic ic_req,
    output logic fill
);
    // Signal declaration
    typedef enum logic [1:0] {
        IDLE,
        MISS,
        FILL
    } state_t;

    state_t state, next_state;

    // State register
    always_ff @(posedge clk) begin
        if (rst)
            state <= IDLE;
        else
            state <= next_state;
    end

    // Next-state logic
    always_comb begin
        next_state = state;

        // Flush always sends the cache back to IDLE
        if (flush) begin
            next_state = IDLE;
        end else begin
            case (state)
                IDLE: begin
                    if (i_req && !cache_hit)
                        next_state = MISS;
                end

                MISS: begin
                    // Keep requesting until the arbiter accepts it
                    if (ic_gnt)
                        next_state = FILL;
                end

                FILL: begin
                    // bus_rdata is ready for this whole cycle
                    next_state = IDLE;
                end

                default: next_state = IDLE;
            endcase
        end
    end

    // Output logic
    always_comb begin
        serve_hit  = 1'b0;
        miss_start = 1'b0;
        ic_req     = 1'b0;
        fill       = 1'b0;

        if (!flush) begin
            case (state)
                IDLE: begin
                    serve_hit  = i_req && cache_hit;
                    miss_start = i_req && !cache_hit;
                end

                MISS: ic_req = 1'b1;

                FILL: fill = 1'b1;

                default: begin
                    serve_hit  = 1'b0;
                    miss_start = 1'b0;
                    ic_req     = 1'b0;
                    fill       = 1'b0;
                end
            endcase
        end
    end
endmodule
