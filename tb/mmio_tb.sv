`timescale 1ns/1ps
//
// Unit testbench for mmio. Directed access to every register, with read data
// checked one cycle after the access.
//
module mmio_tb;

    logic clk = 0, rst = 1;
    always #5 clk = ~clk;

    logic        core_run = 1;
    logic        m_en = 0, m_we = 0, m_core = 0;
    logic [2:0]  m_addr = 0;
    logic [7:0]  m_wdata = 0;
    logic [31:0] m_rdata;
    logic        tx_start;
    logic [7:0]  tx_data;
    logic        tx_busy = 0;
    logic        done, dbg_lock;
    logic [1:0]  dbg_exit;

    mmio dut (.*);

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
            if (n_checks == 0)    $display("FAIL    mmio  no checks written yet");
            else if (n_fail != 0) $display("FAIL    mmio  %0d of %0d checks failed", n_fail, n_checks);
            else                  $display("PASS    mmio  %0d checks", n_checks);
            if (n_checks == 0 || n_fail != 0) $fatal(1, "Simulation failed");
            $finish;
        end
    endtask

    // ---------------- helpers ----------------
    // One bus access in one cycle, as the address decode presents it. The
    // registered read data is returned after the clock edge that ends the access.
    logic [31:0] got;
    logic        start_seen;
    task automatic access(input [2:0] addr, input we, input [7:0] wdata, input core);
        begin
            m_en = 1; m_addr = addr; m_we = we; m_wdata = wdata; m_core = core;
            #1 start_seen = tx_start;
            @(negedge clk);
            m_en = 0; m_we = 0;
            got = m_rdata;
        end
    endtask

    localparam logic [2:0] A_DATA = 3'd0, A_STATUS = 3'd1, A_LOCK = 3'd2,
                           A_ID = 3'd3, A_EXIT = 3'd4, A_PEEK = 3'd5;

    initial begin
        repeat (2) @(negedge clk);
        rst = 0;

        // LOCK: test and set, then release
        access(A_LOCK, 0, 0, 0); check(got == 0, "first LOCK read returns 0");
        check(dbg_lock, "LOCK is set after the read");
        access(A_LOCK, 0, 0, 0); check(got == 1, "second LOCK read returns 1");
        access(A_LOCK, 1, 0, 0); check(!dbg_lock, "LOCK write clears it");
        access(A_LOCK, 0, 0, 1); check(got == 0, "LOCK free again after release");

        // the two-core race: back-to-back reads, only the first wins
        access(A_LOCK, 1, 0, 1);
        access(A_LOCK, 0, 0, 0); check(got == 0, "race: core 0 reads 0 and owns the lock");
        access(A_LOCK, 0, 0, 1); check(got == 1, "race: core 1 reads 1 and must spin");
        access(A_LOCK, 1, 0, 0);

        // LOCK_PEEK reads without setting
        access(A_PEEK, 0, 0, 0); check(got == 0 && !dbg_lock, "LOCK_PEEK does not set the lock");

        // an idle cycle at the LOCK address changes nothing
        m_en = 0; m_addr = A_LOCK; m_we = 0;
        @(negedge clk);
        check(!dbg_lock, "idle cycle at the LOCK address does not set it");
        check(m_rdata == 0, "idle cycle returns 0");

        // CORE_ID
        access(A_ID, 0, 0, 0); check(got == 0, "CORE_ID reads 0 for core 0");
        access(A_ID, 0, 0, 1); check(got == 1, "CORE_ID reads 1 for core 1");

        // UART: tx_start pulses on a write only, carrying the byte
        access(A_DATA, 1, 8'h41, 0); check(start_seen, "UART_DATA write pulses tx_start");
        #1 check(!tx_start, "tx_start lasts one cycle");
        m_en = 1; m_addr = A_DATA; m_we = 1; m_wdata = 8'h5A; #1;
        check(tx_data == 8'h5A, "tx_data carries the written byte");
        @(negedge clk); m_en = 0; m_we = 0;
        access(A_DATA, 0, 0, 0); check(!start_seen && got == 0, "UART_DATA read sends nothing, reads 0");
        tx_busy = 1;
        access(A_STATUS, 0, 0, 0); check(got == 1, "UART_STATUS shows busy");
        tx_busy = 0;
        access(A_STATUS, 0, 0, 0); check(got == 0, "UART_STATUS shows idle");

        // EXIT and done
        check(!done && dbg_exit == 2'b00, "nobody has exited yet");
        access(A_EXIT, 1, 0, 0); check(dbg_exit == 2'b01 && !done, "core 0 exit alone does not raise done");
        access(A_EXIT, 1, 0, 1); check(dbg_exit == 2'b11 && done, "both exits raise done");
        access(A_EXIT, 0, 0, 0); check(got == 3, "EXIT reads back both flags");

        // unused addresses read 0
        access(3'd6, 0, 0, 0); check(got == 0, "address 6 reads 0");
        access(3'd7, 0, 0, 0); check(got == 0, "address 7 reads 0");

        // core_run low clears lock and exit, ready for the next program
        access(A_LOCK, 0, 0, 0);
        check(dbg_lock && done, "lock and done set before the reload");
        core_run = 0;
        @(negedge clk);
        check(!dbg_lock && !done && dbg_exit == 2'b00, "core_run low clears lock and exit");
        core_run = 1;
        access(A_LOCK, 0, 0, 0); check(got == 0, "lock free after the reload");

        report;
    end
endmodule
