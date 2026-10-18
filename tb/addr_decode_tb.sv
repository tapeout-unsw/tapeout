`timescale 1ns/1ps
//
// Unit testbench for addr_decode. Check routing to SRAM and MMIO, m_core,
// and that bus_rdata selects the target of the previous cycle's access.
//
module addr_decode_tb;
    parameter int RAW = 10;

    logic clk = 0, rst = 1;
    always #5 clk = ~clk;

    logic           b_valid = 0;
    logic [12:0]    b_addr = 0;
    logic [31:0]    b_wdata = 0;
    logic [3:0]     b_wstrb = 0;
    logic [2:0]     b_src = 0;
    logic           s_en;
    logic [RAW-3:0] s_addr;
    logic [31:0]    s_wdata;
    logic [3:0]     s_wstrb;
    logic [31:0]    s_rdata = 32'h5555_5555;
    logic           m_en, m_we, m_core;
    logic [2:0]     m_addr;
    logic [7:0]     m_wdata;
    logic [31:0]    m_rdata = 32'hAAAA_AAAA;
    logic [31:0]    bus_rdata;

    addr_decode #(.RAW(RAW)) dut (.*);

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
            if (n_checks == 0)    $display("FAIL    addr_decode  no checks written yet");
            else if (n_fail != 0) $display("FAIL    addr_decode  %0d of %0d checks failed", n_fail, n_checks);
            else                  $display("PASS    addr_decode  %0d checks", n_checks);
            if (n_checks == 0 || n_fail != 0) $fatal(1, "Simulation failed");
            $finish;
        end
    endtask

    // ---------------- helpers ----------------
    // Put one access on the bus. Called just after a falling edge, so the
    // inputs are stable well before the rising edge that registers sel_q.
    task automatic drive(input logic valid, input logic [12:0] addr,
                         input logic [31:0] wdata, input logic [3:0] wstrb,
                         input logic [2:0] src);
        begin
            b_valid = valid; b_addr = addr; b_wdata = wdata;
            b_wstrb = wstrb; b_src = src;
            #1;                                   // let the combinational outputs settle
        end
    endtask

    task automatic idle;
        drive(1'b0, 13'h0000, 32'h0, 4'b0000, 3'd5);
    endtask

    integer i, src;
    logic [12:0] a;

    initial begin
        if ($test$plusargs("waves")) begin
            $dumpfile("addr_decode_tb.vcd");
            $dumpvars(0, addr_decode_tb);
        end
    end

    initial begin
        repeat (2) @(negedge clk);
        rst = 0;

        // ---- T1: nothing on the bus enables nothing ----------------------
        drive(1'b0, 13'h0040, 32'hFFFF_FFFF, 4'b1111, 3'd1);
        check(!s_en && !m_en, "T1 idle, RAM address: s_en/m_en must be 0");
        drive(1'b0, 13'h1008, 32'hFFFF_FFFF, 4'b1111, 3'd2);
        check(!s_en && !m_en, "T1 idle, MMIO address: s_en/m_en must be 0");

        // ---- T2: RAM read reaches only the SRAM --------------------------
        drive(1'b1, 13'h03F0, 32'h1234_5678, 4'b0000, 3'd1);
        check(s_en,                       "T2 RAM read: s_en");
        check(!m_en,                      "T2 RAM read: m_en must be 0");
        check(s_addr == 13'h03F0 >> 2,    "T2 RAM read: s_addr = byte addr >> 2");
        check(s_wstrb == 4'b0000,         "T2 RAM read: s_wstrb passes through");

        // ---- T3: RAM write passes data and strobes unchanged -------------
        drive(1'b1, 13'h0004, 32'hDEAD_BEEF, 4'b0110, 3'd2);
        check(s_en && !m_en,              "T3 RAM write: only s_en");
        check(s_addr == 1,                "T3 RAM write: s_addr");
        check(s_wdata == 32'hDEAD_BEEF,   "T3 RAM write: s_wdata");
        check(s_wstrb == 4'b0110,         "T3 RAM write: s_wstrb");

        // ---- T4: RAM address edges ---------------------------------------
        drive(1'b1, 13'h0000, 32'h0, 4'b0000, 3'd1);
        check(s_en && s_addr == 0,                    "T4 first word");
        drive(1'b1, (1 << RAW) - 4, 32'h0, 4'b0000, 3'd1);
        check(s_en && s_addr == (1 << (RAW-2)) - 1,   "T4 last word");
        drive(1'b1, 13'h0FFC, 32'h0, 4'b0000, 3'd1);
        check(s_en && !m_en,                          "T4 0x0FFC is RAM, not MMIO");

        // ---- T5: every MMIO register address -----------------------------
        for (i = 0; i < 8; i = i + 1) begin
            a = 13'h1000 + i * 4;
            drive(1'b1, a, 32'h0, 4'b0000, 3'd1);
            check(m_en && !s_en,     "T5 MMIO: only m_en");
            check(m_addr == i,       "T5 MMIO: m_addr = register index");
            check(!m_we,             "T5 MMIO read: m_we must be 0");
        end

        // ---- T6: MMIO write ----------------------------------------------
        drive(1'b1, 13'h1000, 32'hAABB_CC41, 4'b0001, 3'd1);
        check(m_we,                       "T6 MMIO write wstrb=0001: m_we");
        check(m_wdata == 8'h41,           "T6 MMIO write: m_wdata = b_wdata[7:0]");
        drive(1'b1, 13'h1010, 32'h0, 4'b1000, 3'd1);
        check(m_we,                       "T6 MMIO write wstrb=1000: m_we");
        drive(1'b1, 13'h1010, 32'h0, 4'b1111, 3'd1);
        check(m_we,                       "T6 MMIO write wstrb=1111: m_we");

        // ---- T7: m_core for every requester ------------------------------
        for (src = 0; src < 6; src = src + 1) begin
            drive(1'b1, 13'h100C, 32'h0, 4'b0000, src[2:0]);
            check(m_core == (src == 2), "T7 m_core is 1 only for b_src = 2");
        end

        // ---- T8: read data comes from the previous cycle's target --------
        drive(1'b1, 13'h0010, 32'h0, 4'b0000, 3'd1);   // RAM access
        @(negedge clk);                                // its data cycle
        idle;
        check(bus_rdata == s_rdata, "T8 after a RAM access: bus_rdata = s_rdata");

        drive(1'b1, 13'h1004, 32'h0, 4'b0000, 3'd1);   // MMIO access
        @(negedge clk);
        idle;
        check(bus_rdata == m_rdata, "T8 after an MMIO access: bus_rdata = m_rdata");

        // ---- T9: back to back, RAM then MMIO then RAM --------------------
        drive(1'b1, 13'h0020, 32'h0, 4'b0000, 3'd1);   // cycle 1: RAM
        check(bus_rdata == m_rdata, "T9 cycle 1 still shows the old MMIO data");
        @(negedge clk);
        drive(1'b1, 13'h1008, 32'h0, 4'b0000, 3'd2);   // cycle 2: MMIO
        check(bus_rdata == s_rdata, "T9 cycle 2 returns cycle 1's RAM data");
        @(negedge clk);
        drive(1'b1, 13'h0024, 32'h0, 4'b0000, 3'd1);   // cycle 3: RAM
        check(bus_rdata == m_rdata, "T9 cycle 3 returns cycle 2's MMIO data");
        @(negedge clk);
        idle;
        check(bus_rdata == s_rdata, "T9 cycle 4 returns cycle 3's RAM data");

        // ---- T10: changing rdata passes straight through -----------------
        drive(1'b1, 13'h0030, 32'h0, 4'b0000, 3'd1);
        @(negedge clk);
        idle;
        s_rdata = 32'h0BAD_F00D; #1;
        check(bus_rdata == 32'h0BAD_F00D, "T10 bus_rdata follows s_rdata combinationally");
        s_rdata = 32'h5555_5555;

        report;
    end
endmodule
