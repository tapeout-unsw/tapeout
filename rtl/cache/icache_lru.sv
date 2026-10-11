`timescale 1ns / 1ps
//
// LRU for the 2-way instruction cache. One bit per set.
//
// lru[set] names the victim way (the least recently used one).
// On a hit or fill to way w, lru[set] becomes ~w, so the other way
// is evicted next.
//
module icache_lru (
    input  logic       clk,
    input  logic       rst,

    input  logic [2:0] index,       // set being accessed
    input  logic       access,      // high on a hit or a fill this cycle
    input  logic       access_way,  // way that was hit or filled

    output logic       victim_way   // way to replace on a miss
);
    logic lru [0:7];

    assign victim_way = lru[index];

    always_ff @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < 8; i++)
                lru[i] <= 1'b0;
        end else if (access) begin
            lru[index] <= ~access_way;
        end
    end
endmodule