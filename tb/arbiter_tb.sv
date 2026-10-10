`timescale 1ns/1ps
//
// Unit testbench for arbiter. Drive the five request bundles, check grants and
// the forwarded b_* bundle against the rules in rtl/bus/arbiter.sv.
//
module arbiter_tb;

    logic clk = 0, rst = 1;
    always #5 clk = ~clk;

    logic        loading = 0;
    logic        boot_req = 0;  logic [12:0] boot_addr = 0; logic [31:0] boot_wdata = 0; logic [3:0] boot_wstrb = 4'b1111;
    logic        d0_req = 0;    logic [12:0] d0_addr = 0;   logic [31:0] d0_wdata = 0;   logic [3:0] d0_wstrb = 0;   logic d0_gnt;
    logic        d1_req = 0;    logic [12:0] d1_addr = 0;   logic [31:0] d1_wdata = 0;   logic [3:0] d1_wstrb = 0;   logic d1_gnt;
    logic        ic0_req = 0;   logic [12:0] ic0_addr = 0;  logic [31:0] ic0_wdata = 0;  logic [3:0] ic0_wstrb = 0;  logic ic0_gnt;
    logic        ic1_req = 0;   logic [12:0] ic1_addr = 0;  logic [31:0] ic1_wdata = 0;  logic [3:0] ic1_wstrb = 0;  logic ic1_gnt;
    logic        b_valid;
    logic [12:0] b_addr;
    logic [31:0] b_wdata;
    logic [3:0]  b_wstrb;
    logic [2:0]  b_src;

    arbiter dut (.*);

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
            if (n_checks == 0)    $display("FAIL    arbiter  no checks written yet");
            else if (n_fail != 0) $display("FAIL    arbiter  %0d of %0d checks failed", n_fail, n_checks);
            else                  $display("PASS    arbiter  %0d checks", n_checks);
            if (n_checks == 0 || n_fail != 0) $fatal(1, "Simulation failed");
            $finish;
        end
    endtask

    // ---------------- helpers ----------------
    // Requesters 1..4 as arrays, mapped onto the named ports.
    logic        r_req   [1:4];
    logic [12:0] r_addr  [1:4];
    logic [31:0] r_wdata [1:4];
    logic [3:0]  r_wstrb [1:4];
    logic        r_gnt   [1:4];
    always_comb begin
        d0_req  = r_req[1]; d0_addr  = r_addr[1]; d0_wdata  = r_wdata[1]; d0_wstrb  = r_wstrb[1];
        d1_req  = r_req[2]; d1_addr  = r_addr[2]; d1_wdata  = r_wdata[2]; d1_wstrb  = r_wstrb[2];
        ic0_req = r_req[3]; ic0_addr = r_addr[3]; ic0_wdata = r_wdata[3]; ic0_wstrb = r_wstrb[3];
        ic1_req = r_req[4]; ic1_addr = r_addr[4]; ic1_wdata = r_wdata[4]; ic1_wstrb = r_wstrb[4];
        r_gnt[1] = d0_gnt; r_gnt[2] = d1_gnt; r_gnt[3] = ic0_gnt; r_gnt[4] = ic1_gnt;
    end

    function automatic integer n_grants();
        n_grants = d0_gnt + d1_gnt + ic0_gnt + ic1_gnt;
    endfunction

    task automatic clear_all;
        for (int k = 1; k <= 4; k++) begin
            r_req[k] = 0; r_addr[k] = 0; r_wdata[k] = 0; r_wstrb[k] = 0;
        end
        boot_req = 0;
    endtask

    // The granted bundle must appear on b_* with its index on b_src.
    task automatic check_forward(input integer k, input [8*64:1] what);
        check(b_valid && b_src == 3'(k) && b_addr == r_addr[k] &&
              b_wdata == r_wdata[k] && b_wstrb == r_wstrb[k], what);
    endtask

    integer seed, waited [1:4], granted_k, grants_seen [1:4], cycles;
    logic   was_granted [1:4];

    initial begin
        if (!$value$plusargs("SEED=%d", seed)) seed = 1;
        $display("arbiter_tb seed=%0d", seed);
        void'($urandom(seed));
        clear_all();
        repeat (2) @(negedge clk);
        rst = 0;

        // 1. idle: no requests, no grants
        @(negedge clk); #1;
        check(n_grants() == 0 && !b_valid, "idle bus grants nobody");

        // 2. one requester alone is granted in the same cycle, bundle forwarded
        for (int k = 1; k <= 4; k++) begin
            clear_all();
            r_req[k] = 1; r_addr[k] = 13'(16 * k); r_wdata[k] = 32'hA000_0000 + k; r_wstrb[k] = 4'(k);
            #1;
            check(r_gnt[k] && n_grants() == 1, "lone requester granted in the same cycle");
            check_forward(k, "lone requester's bundle forwarded");
            @(negedge clk);
        end
        clear_all();

        // 3. loading: only the bootloader is granted, cores are ignored
        loading = 1;
        for (int k = 1; k <= 4; k++) r_req[k] = 1;
        boot_req = 1; boot_addr = 13'h0040; boot_wdata = 32'hB007_B007;
        #1;
        check(n_grants() == 0, "no core grant while loading");
        check(b_valid && b_src == 3'd0 && b_addr == 13'h0040 && b_wdata == 32'hB007_B007 &&
              b_wstrb == 4'b1111, "boot write forwarded while loading");
        boot_req = 0;
        #1;
        check(!b_valid && n_grants() == 0, "loading without boot_req grants nobody");
        @(negedge clk);
        loading = 0;
        clear_all();

        // 4. all four held high: strict rotation, each served once per 4 cycles
        for (int k = 1; k <= 4; k++) begin r_req[k] = 1; grants_seen[k] = 0; end
        granted_k = 0;
        for (int c = 0; c < 16; c++) begin
            #1;
            check(n_grants() == 1, "exactly one grant under full load");
            for (int k = 1; k <= 4; k++)
                if (r_gnt[k]) begin
                    if (granted_k != 0)
                        check(k == (granted_k % 4) + 1, "grants rotate to the next source");
                    granted_k = k;
                    grants_seen[k]++;
                end
            @(negedge clk);
        end
        for (int k = 1; k <= 4; k++) check(grants_seen[k] == 4, "each source granted 4 times in 16 cycles");
        clear_all();

        // 5. random traffic obeying the bus rules: hold the bundle until granted,
        //    then optionally request again with a new bundle
        for (int k = 1; k <= 4; k++) waited[k] = 0;
        for (cycles = 0; cycles < 20000; cycles++) begin
            for (int k = 1; k <= 4; k++)
                if (!r_req[k] && ($urandom % 3 == 0)) begin
                    r_req[k] = 1; r_addr[k] = 13'($urandom); r_wdata[k] = $urandom; r_wstrb[k] = 4'($urandom);
                    waited[k] = 0;
                end
            #1;
            check(n_grants() <= 1, "never more than one grant");
            check(b_valid == (n_grants() == 1), "b_valid matches a grant");
            for (int k = 1; k <= 4; k++) begin
                was_granted[k] = r_gnt[k];
                if (r_gnt[k]) begin
                    check(r_req[k], "grant only to a requester");
                    check_forward(k, "granted bundle forwarded intact");
                end
                if (r_req[k] && !r_gnt[k]) begin
                    waited[k]++;
                    check(waited[k] <= 3, "no requester waits more than 3 cycles");
                end
            end
            check(n_grants() == 1 || !(r_req[1] || r_req[2] || r_req[3] || r_req[4]),
                  "someone is granted whenever anyone asks");
            @(negedge clk);
            // drop the request granted in the cycle just checked, as a
            // requester does after seeing its grant at the clock edge
            for (int k = 1; k <= 4; k++) if (was_granted[k]) r_req[k] = 0;
        end

        report;
    end
endmodule
