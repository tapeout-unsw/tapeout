`timescale 1ns / 1ps
//
// Data cache controller. Decides, every cycle, what a core data access does:
// answer from the cache, go to the bus, or both over two cycles. It keeps the
// core's data-port behaviour (core.sv) so the core needs no change.
//
// Policy: write-through, no allocate on a store miss, snoop-invalidate
// (dcache_snoop.sv). The SRAM always holds the latest value, so there are no
// dirty lines.
//
// Core side (as in core.sv)
//   d_req    the core wants an access. Address, data and strobes are stable
//            while d_req is high. The core waits in S_MEM until d_gnt.
//   d_gnt    the access was accepted this cycle. A store is then complete; a
//            load's data is on d_rdata in the NEXT cycle.
//   d_mmio   d_addr[12]: MMIO, never cached.
//   d_store  |d_wstrb: a store. A load has d_wstrb = 0000.
//
// Bus side
//   dc_req   request to the arbiter (the arbiter's dX_req). dc_gnt is its
//            grant, combinational, one access per cycle. The load's data is on
//            bus_rdata in the cycle after dc_gnt.
//
// What happens in IDLE with d_req high
//   MMIO, or dcache_off   pass through: dc_req = 1, d_gnt = dc_gnt. A load
//                         then goes to PASS, to hand bus_rdata to the core.
//                         Nothing is installed.
//   store                 write-through: dc_req = 1, d_gnt = dc_gnt. On the
//                         grant, if the line is cached, store_hit tells the
//                         datapath to merge the strobed bytes into it. A
//                         store miss does not allocate.
//   load hit              d_gnt = 1 at once, no bus request. serve_hit tells
//                         the datapath to register the line's data; it is on
//                         d_rdata next cycle (resp_bus = 0).
//   load miss             dc_req = 1, d_gnt = dc_gnt. miss_start (the grant
//                         cycle) tells the datapath to save the miss address
//                         and pick the victim way. Go to FILL.
//
// FILL (the cycle after a load miss was granted)
//   bus_rdata is the word and resp_bus = 1 (d_rdata = bus_rdata). The line is
//   installed (fill) unless kill_fill is high: the snoop saw another requester
//   write that word in this very cycle, so the data read in the grant cycle is
//   already stale. The core still gets that data (its load came before the
//   write in bus order); the cache just must not keep it. Go to IDLE.
//
// PASS (the cycle after a pass-through load was granted)
//   resp_bus = 1. Go to IDLE.
//
// d_rdata must be driven as: resp_bus ? bus_rdata : registered hit data.
//
// flush and rst return the FSM to IDLE and drop every output, as in the
// I-cache (flush is high while the bootloader loads a program). They clear the
// valid bits, which is the storage's job.
//
// dcache_off (the chicken bit) must only change while flush is high: a store
// that passes through while off would not update a line cached earlier.
//
module dcache_FSM (
    input  logic clk,
    input  logic rst,
    input  logic flush,
    input  logic dcache_off,

    // Core side
    input  logic d_req,
    input  logic d_mmio,
    input  logic d_store,
    output logic d_gnt,

    // Lookup of d_addr in both ways (valid and tag match)
    input  logic cache_hit,

    // Bus side
    output logic dc_req,
    input  logic dc_gnt,

    // From dcache_snoop: do not install the line being filled
    input  logic kill_fill,

    // Controls for the datapath in dcache.sv
    output logic serve_hit,     // load hit: register the hit data for next cycle
    output logic miss_start,    // load miss granted: save address, pick victim
    output logic store_hit,     // store granted and cached: merge bytes into the line
    output logic in_fill,       // FILL cycle: dcache_snoop watches for a write to the miss
    output logic fill,          // FILL and not killed: write the line from bus_rdata
    output logic resp_bus       // d_rdata comes from bus_rdata this cycle
);
    typedef enum logic [1:0] {
        IDLE,
        FILL,
        PASS
    } state_t;

    state_t state, next_state;

    logic bypass;
    assign bypass = dcache_off || d_mmio;

    // State register
    always_ff @(posedge clk) begin
        if (rst)
            state <= IDLE;
        else
            state <= next_state;
    end

    // Next-state logic
    always_comb begin
        next_state = IDLE;

        if (!flush && state == IDLE && d_req && dc_gnt && !d_store) begin
            if (bypass)
                next_state = PASS;
            else if (!cache_hit)
                next_state = FILL;
        end
    end

    // Output logic
    always_comb begin
        d_gnt      = 1'b0;
        dc_req     = 1'b0;
        serve_hit  = 1'b0;
        miss_start = 1'b0;
        store_hit  = 1'b0;
        in_fill    = 1'b0;
        fill       = 1'b0;
        resp_bus   = 1'b0;

        if (!flush) begin
            case (state)
                IDLE: begin
                    if (d_req) begin
                        if (bypass) begin
                            dc_req = 1'b1;
                            d_gnt  = dc_gnt;
                        end else if (d_store) begin
                            dc_req    = 1'b1;
                            d_gnt     = dc_gnt;
                            store_hit = dc_gnt && cache_hit;
                        end else if (cache_hit) begin
                            d_gnt     = 1'b1;
                            serve_hit = 1'b1;
                        end else begin
                            dc_req     = 1'b1;
                            d_gnt      = dc_gnt;
                            miss_start = dc_gnt;
                        end
                    end
                end

                FILL: begin
                    in_fill  = 1'b1;
                    fill     = !kill_fill;
                    resp_bus = 1'b1;
                end

                PASS: resp_bus = 1'b1;

                default: ;
            endcase
        end
    end
endmodule
