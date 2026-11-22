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
    logic        d0_atomic = 0, d1_atomic = 0;   // atomics: sections 6 and 7
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

    // atomics: the hold model and each data port's atomic sequence
    // (amo_st 0 = none, 1 = atomic read pending, 2 = atomic write due)
    integer other, amo_st [1:4], m_owner, n_holds;
    logic   m_busy;

    task automatic set_atomic(input integer k, input logic v);
        if (k == 1) d0_atomic = v; else if (k == 2) d1_atomic = v;
    endtask

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
        clear_all();
        @(negedge clk);

        // 6. atomics (ARB-4): a granted atomic read by core k starts a hold;
        //    while it lasts only core k can win; core k's next write ends it.
        for (int owner = 1; owner <= 2; owner++) begin
            other = 3 - owner;
            // the atomic read, alone so it wins whatever the pointer
            r_req[owner] = 1; r_wstrb[owner] = 0; r_addr[owner] = 13'h0300;
            set_atomic(owner, 1);
            #1;
            check(r_gnt[owner] && n_grants() == 1, "atomic read granted");
            @(negedge clk);
            // the owner idles while holding; everyone else asks, including an
            // atomic read by the other core and the bootloader
            r_req[owner] = 0;
            r_req[other] = 1; r_wstrb[other] = 0; set_atomic(other, 1);
            r_req[3] = 1; r_req[4] = 1; boot_req = 1;
            for (int c = 0; c < 4; c++) begin
                #1;
                check(!b_valid && n_grants() == 0, "hold: owner idle, nobody else granted");
                @(negedge clk);
            end
            // the owner reads again during the hold: granted, hold continues
            r_req[owner] = 1; r_wstrb[owner] = 0; r_addr[owner] = 13'h0304;
            #1;
            check(r_gnt[owner] && n_grants() == 1, "hold: owner's read granted");
            check_forward(owner, "hold: owner's read forwarded");
            @(negedge clk);
            r_req[owner] = 0;
            #1;
            check(!b_valid && n_grants() == 0, "hold survives a read by the owner");
            @(negedge clk);
            // the owner's write ends the hold
            r_req[owner] = 1; r_wstrb[owner] = 4'b1111; r_wdata[owner] = 32'hA70A_0000 + owner;
            #1;
            check(r_gnt[owner] && n_grants() == 1, "hold: owner's write granted");
            check_forward(owner, "hold: owner's write forwarded");
            @(negedge clk);
            r_req[owner] = 0; set_atomic(owner, 0);
            #1;
            check(b_valid && n_grants() == 1 && !r_gnt[owner], "hold released after owner's write");
            set_atomic(other, 0);
            clear_all();
            @(negedge clk);
        end

        // 6b. accesses that must NOT start a hold
        //   - a plain read by core 0 with d0_atomic low
        //   - a read by core 1 while only d0_atomic is high (wrong core's flag)
        //   - a write by core 0 with d0_atomic high
        for (int t = 0; t < 4; t++) begin
            clear_all();
            case (t)
                0: begin r_req[1] = 1; set_atomic(1, 0); end
                1: begin r_req[2] = 1; set_atomic(1, 1); set_atomic(2, 0); end
                2: begin r_req[1] = 1; r_wstrb[1] = 4'b0011; set_atomic(1, 1); end
                3: begin r_req[2] = 1; r_wstrb[2] = 4'b1000; set_atomic(2, 1); end
            endcase
            #1;
            check(n_grants() == 1, "lone data access granted");
            @(negedge clk);
            clear_all(); set_atomic(1, 0); set_atomic(2, 0);
            r_req[3] = 1;
            #1;
            check(ic0_gnt, "no hold after a non-atomic or write access");
            @(negedge clk);
        end
        clear_all();

        // 7. random traffic with atomic read/write pairs on both data ports,
        //    checked against a model of the hold
        m_busy = 0; m_owner = 0;
        for (int k = 1; k <= 4; k++) begin waited[k] = 0; amo_st[k] = 0; end
        n_holds = 0;
        for (cycles = 0; cycles < 20000; cycles++) begin
            for (int k = 1; k <= 4; k++) begin
                if (!r_req[k] && amo_st[k] == 0 && ($urandom % 3 == 0)) begin
                    r_req[k] = 1; r_addr[k] = 13'($urandom); r_wdata[k] = $urandom;
                    waited[k] = 0;
                    if (k <= 2 && ($urandom % 4 == 0)) begin
                        amo_st[k] = 1; r_wstrb[k] = 0; set_atomic(k, 1);      // atomic read
                    end else
                        r_wstrb[k] = 4'($urandom);
                end else if (!r_req[k] && amo_st[k] == 2 && ($urandom % 2 == 0)) begin
                    r_req[k] = 1; r_wstrb[k] = 4'b1111; r_wdata[k] = $urandom;  // atomic write
                end
            end
            #1;
            check(n_grants() <= 1, "atomics: never more than one grant");
            check(b_valid == (n_grants() == 1), "atomics: b_valid matches a grant");
            for (int k = 1; k <= 4; k++) begin
                was_granted[k] = r_gnt[k];
                if (r_gnt[k]) begin
                    check(r_req[k], "atomics: grant only to a requester");
                    check_forward(k, "atomics: granted bundle forwarded intact");
                    if (m_busy) check(k == m_owner, "atomics: only the owner wins during a hold");
                end
                if (m_busy) waited[k] = 0;
                else if (r_req[k] && !r_gnt[k]) begin
                    waited[k]++;
                    check(waited[k] <= 3, "atomics: no requester waits more than 3 cycles outside a hold");
                end
            end
            if (!m_busy)
                check(n_grants() == 1 || !(r_req[1] || r_req[2] || r_req[3] || r_req[4]),
                      "atomics: someone is granted whenever anyone asks outside a hold");
            else
                check(n_grants() == (r_req[m_owner] ? 1 : 0), "atomics: owner granted whenever it asks");
            // model update at the edge
            if (m_busy) begin
                if (r_gnt[m_owner] && r_wstrb[m_owner] != 0) m_busy = 0;
            end else
                for (int k = 1; k <= 2; k++)
                    if (r_gnt[k] && r_wstrb[k] == 0 && (k == 1 ? d0_atomic : d1_atomic)) begin
                        m_busy = 1; m_owner = k; n_holds++;
                    end
            @(negedge clk);
            for (int k = 1; k <= 4; k++) if (was_granted[k]) begin
                r_req[k] = 0;
                if (amo_st[k] == 1) amo_st[k] = 2;
                else if (amo_st[k] == 2) begin amo_st[k] = 0; set_atomic(k, 0); end
            end
        end
        check(n_holds > 100, "atomics: random test produced many holds");
        $display("arbiter_tb: %0d atomic holds in the random test", n_holds);

        report;
    end
endmodule
