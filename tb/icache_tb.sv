`timescale 1ns/1ps
//
// Unit testbench for icache. Acts as the core (i_*) and as the bus
// (ic_gnt, bus_rdata) backed by a reference memory. Replay a PC sequence and
// check every returned instruction.
//
// +TRACE=<file> loads a fetch address sequence, one hex value per line, into
// trace[0 .. n_trace-1]. Record one from a real program with
//   vvp <core_mc_tb build> +HEX=tests/<name>.hex +FETCHTRACE=<file>
// Addresses are 13-bit; with RAW=10 keep traces from programs under 1 KiB.
//
module icache_tb;
    parameter int RAW = 10;

    logic clk = 0, rst = 1;
    always #5 clk = ~clk;

    logic        flush = 0;
    logic        off = 0;
    logic        i_req = 0;
    logic [12:0] i_addr = 0;
    logic        i_valid;
    logic [31:0] i_rdata;
    logic        ic_req;
    logic [12:0] ic_addr;
    logic [31:0] ic_wdata;
    logic [3:0]  ic_wstrb;
    logic        ic_gnt;
    logic [31:0] bus_rdata;

    icache #(.RAW(RAW)) dut (.*);

    // ---------------- checks ----------------
    integer n_checks = 0, n_fail = 0;
    task automatic check(input logic cond, input [8*64:1] what);
        begin
            n_checks = n_checks + 1;
            if (!cond) begin
                n_fail = n_fail + 1;
                $display("  check failed at %0t: %0s", $time, what);
            end
        end
    endtask

    task automatic report;
        begin
            if (n_checks == 0)    $display("FAIL    icache  no checks written yet");
            else if (n_fail != 0) $display("FAIL    icache  %0d of %0d checks failed", n_fail, n_checks);
            else                  $display("PASS    icache  %0d checks", n_checks);
            if (n_checks == 0 || n_fail != 0) $fatal(1, "Simulation failed");
            $finish;
        end
    endtask

    // Bus model: grants a pending request after a random 0 to 3 cycle delay
    // and returns mem[word] the cycle after the grant.
    logic [31:0] mem [0:(1<<(RAW-2))-1];
    integer      wait_left = 0;
    always_comb  ic_gnt = ic_req && (wait_left == 0);
    always_ff @(posedge clk) begin
        if (ic_req && !ic_gnt) wait_left <= wait_left - 1;
        else                   wait_left <= $urandom_range(0, 3);
        if (ic_gnt) bus_rdata <= mem[ic_addr[RAW-1:2]];
    end

    // ---------------- core side ----------------
    // fetch(addr): hold i_req until i_valid, check the word against mem.
    // Counts fetches answered in the same cycle (hits) in n_same_cycle.
    integer n_fetches = 0, n_same_cycle = 0;
    task automatic fetch(input logic [12:0] addr);
        integer waited;
        begin
            @(negedge clk);
            i_req  = 1;
            i_addr = addr;
            waited = 0;
            #1;
            while (!i_valid && waited < 100) begin
                @(negedge clk);
                waited = waited + 1;
                #1;
            end
            check(i_valid, "fetch timed out");
            check(i_rdata == mem[addr[RAW-1:2]], "fetched word differs from memory");
            n_fetches = n_fetches + 1;
            if (waited == 0) n_same_cycle = n_same_cycle + 1;
            @(negedge clk);
            i_req = 0;
        end
    endtask

    // ---------------- trace ----------------
    logic [12:0] trace [0:65535];
    integer      n_trace = 0;
    string       tracefile;

    integer i;
    initial begin
        for (i = 0; i < (1<<(RAW-2)); i = i + 1) mem[i] = $urandom;
        if ($value$plusargs("TRACE=%s", tracefile)) begin
            for (i = 0; i < 65536; i = i + 1) trace[i] = 'x;
            $readmemh(tracefile, trace);
            while (n_trace < 65536 && trace[n_trace] !== 'x) n_trace = n_trace + 1;
            $display("loaded %0d fetch addresses from %0s", n_trace, tracefile);
        end
        repeat (2) @(negedge clk);
        rst = 0;

        // Write tests here. Suggested:
        //   - fetch one address: i_valid rises, i_rdata == mem[addr]
        //   - loop over 16 addresses twice: every word correct; with the
        //     cache, the second pass is all hits
        //   - two addresses mapping to the same set: both stay cached
        //   - flush mid-sequence: the next fetch misses
        //   - replay trace[0 .. n_trace-1] with fetch(); report n_same_cycle

        report;
    end
endmodule
