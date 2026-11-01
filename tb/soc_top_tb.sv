`timescale 1ns/1ps
//
// Dual-core soc_top testbench. Loads +HEX over the serial pin and runs it on
// both cores. Every cycle it checks fetched instructions against the image,
// RAM reads on the bus and every load a core receives (bus or data cache)
// against a shadow copy of RAM, and arbiter grants. DCACHE goes to soc_top.
//
// Without +DONE the first UART byte must be 'P' (riscv-tests). With +DONE both
// cores must write EXIT and the UART text must equal +UART (default "P").
// An explicit empty +UART= skips the UART comparison in DONE mode.
// +HEX2, +HEX3, ... load further images in order without a reset.
// +DEBUG stops on EXIT or +CYCLES without requiring a UART pass message.
// +DUMP_REGS=0 disables final register dumps; +TRACE_REGS=1 dumps each retire.
// +PRINT_UART=0 disables printing received UART bytes.
// +REG1=0:x5=ff +REG2=1:x5=ff00 checks final registers, values in hex.
//
module soc_top_tb;

    parameter integer NREGS     = 16;
    localparam integer RAW      = 11;
    localparam integer WORDS    = 1 << (RAW - 2);
    parameter integer CLK_FREQ  = 2_000_000;
    parameter integer BAUD_RATE = 100_000;
    localparam integer CPB      = CLK_FREQ / BAUD_RATE;
    parameter integer TIMEOUT   = 3_000_000;
    parameter integer DCACHE    = 0;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg  rx_pin = 1'b1;
    wire tx_pin, core_run, done, dbg_lock;
    wire [1:0] dbg_exit;

    soc_top #(.NREGS(NREGS), .RAW(RAW),
              .CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE), .DCACHE(DCACHE)) dut (
        .clk(clk), .rst(rst),
        .uart_rx(rx_pin), .uart_tx(tx_pin), .done(done),
        .dbg_core_run(core_run), .dbg_lock(dbg_lock), .dbg_exit(dbg_exit)
    );

    integer cycle = 0;
    always @(posedge clk) cycle <= cycle + 1;

    integer cycle_limit = TIMEOUT, trace_regs = 0, dump_end = 1, print_uart = 1;
    reg debug_mode = 0;

    task automatic dump_registers(input integer c, input logic [31:0] pc);
        $display("REGS core=%0d pc=%08x", c, pc);
        for (int r = 0; r < NREGS; r++) begin
            if (c == 0) $write(" x%0d=%08x", r, r == 0 ? 32'd0 : dut.u_core0.u_rf.regs[r]);
            else        $write(" x%0d=%08x", r, r == 0 ? 32'd0 : dut.u_core1.u_rf.regs[r]);
            if (r % 4 == 3) $write("\n");
        end
    endtask

    // Save the retiring PC before the edge, then print after register writeback.
    always @(posedge clk) begin : trace_step
        logic completed0, completed1;
        logic [31:0] pc0, pc1;
        completed0 = !rst && core_run && dut.c0_retire;
        completed1 = !rst && core_run && dut.c1_retire;
        pc0 = dut.c0_retire_pc;
        pc1 = dut.c1_retire_pc;
        #1;
        if (trace_regs != 0) begin
            if (completed0) dump_registers(0, pc0);
            if (completed1) dump_registers(1, pc1);
        end
    end

    // Print both cores' final register values when the simulation finishes.
    final begin
        if (dump_end != 0) begin
            dump_registers(0, dut.c0_retire_pc);
            dump_registers(1, dut.c1_retire_pc);
        end
    end

    integer n_errors = 0;
    task automatic error(input string what);
        begin
            n_errors = n_errors + 1;
            if (n_errors <= 10) $display("ERROR   cycle %0d: %0s", cycle, what);
        end
    endtask

    typedef struct packed {
        integer core_id;
        integer reg_index;
        logic [31:0] value;
    } register_expectation;
    register_expectation register_checks[$];

    task automatic read_register_checks;
        register_expectation expected;
        string spec, value_text;
        integer index, parsed;
        begin
            index = 1;
            while ($value$plusargs($sformatf("REG%0d=%%s", index), spec)) begin
                parsed = $sscanf(spec, "%d:x%d=%s", expected.core_id,
                                 expected.reg_index, value_text);
                if (parsed != 3)
                    $fatal(1, "Invalid register expectation '%s'; use core:xN=hex", spec);
                if (expected.core_id < 0 || expected.core_id > 1)
                    $fatal(1, "Invalid core in '%s'; expected 0 or 1", spec);
                if (expected.reg_index < 0 || expected.reg_index >= NREGS)
                    $fatal(1, "Invalid register in '%s'; expected x0 through x%0d", spec, NREGS-1);
                if (value_text.len() >= 2 &&
                    (value_text.substr(0, 1) == "0x" || value_text.substr(0, 1) == "0X"))
                    value_text = value_text.substr(2, value_text.len()-1);
                if (value_text.len() < 1 || value_text.len() > 8)
                    $fatal(1, "Invalid value in '%s'; expected 1 to 8 hex digits", spec);
                for (int digit = 0; digit < value_text.len(); digit++) begin
                    if (!((value_text[digit] >= "0" && value_text[digit] <= "9") ||
                          (value_text[digit] >= "a" && value_text[digit] <= "f") ||
                          (value_text[digit] >= "A" && value_text[digit] <= "F")))
                        $fatal(1, "Invalid hex value in '%s'", spec);
                end
                parsed = $sscanf(value_text, "%h", expected.value);
                if (parsed != 1) $fatal(1, "Invalid hex value in '%s'", spec);
                register_checks.push_back(expected);
                index = index + 1;
            end
        end
    endtask

    task automatic check_registers;
        register_expectation expected;
        logic [31:0] actual;
        begin
            foreach (register_checks[index]) begin
                expected = register_checks[index];
                if (expected.reg_index == 0) actual = 32'd0;
                else if (expected.core_id == 0)
                    actual = dut.u_core0.u_rf.regs[expected.reg_index];
                else
                    actual = dut.u_core1.u_rf.regs[expected.reg_index];
                if (actual !== expected.value)
                    error($sformatf("core %0d x%0d = %08x, expected %08x",
                                    expected.core_id, expected.reg_index, actual, expected.value));
                else
                    $display("REGPASS core=%0d x%0d=%08x", expected.core_id, expected.reg_index, actual);
            end
        end
    endtask

    task automatic send_byte(input [7:0] b);
        integer i;
        begin
            rx_pin = 1'b0;
            repeat (CPB) @(negedge clk);
            for (i = 0; i < 8; i = i + 1) begin
                rx_pin = b[i];
                repeat (CPB) @(negedge clk);
            end
            rx_pin = 1'b1;
            repeat (CPB) @(negedge clk);
        end
    endtask

    reg [7:0] got [0:63];
    integer   ngot = 0;

    initial begin : rx_monitor
        reg [7:0] b;
        integer   i;
        forever begin
            @(negedge tx_pin);
            repeat (CPB + CPB/2) @(posedge clk);
            for (i = 0; i < 8; i = i + 1) begin
                b[i] = tx_pin;
                repeat (CPB) @(posedge clk);
            end
            if (ngot < 64) got[ngot] = b;
            ngot = ngot + 1;
            if (print_uart != 0) $display("UART    rx=0x%02x '%c'", b, b);
        end
    end

    reg [31:0] img       [0:WORDS-1];
    reg [31:0] golden    [0:WORDS-1];
    reg        golden_ok [0:WORDS-1];
    integer    nwords;

    task automatic read_image(input string file);
        integer i, fd;
        begin
            fd = $fopen(file, "r");
            if (fd == 0) $fatal(1, "Cannot open image: %s", file);
            $fclose(fd);
            $display("LOAD    %0s", file);
            for (i = 0; i < WORDS; i = i + 1) img[i] = 32'hFFFF_FFFF;
            $readmemh(file, img);
            nwords = 0;
            for (i = 0; i < WORDS; i = i + 1)
                if (img[i] !== 32'hFFFF_FFFF) nwords = i + 1;
        end
    endtask

    task automatic load_image;
        integer i;
        begin
            send_byte(nwords[7:0]);
            send_byte(nwords[15:8]);
            send_byte(nwords[23:16]);
            send_byte(nwords[31:24]);
            if (core_run) error("cores still running after the word count");
            for (i = 0; i < WORDS; i = i + 1) begin
                golden[i]    = img[i];
                golden_ok[i] = (i < nwords);
            end
            for (i = 0; i < nwords; i = i + 1) begin
                send_byte(img[i][7:0]);
                send_byte(img[i][15:8]);
                send_byte(img[i][23:16]);
                send_byte(img[i][31:24]);
            end
            if (!core_run) error("bootloader did not release the cores");
        end
    endtask

    task automatic check_fetch(input integer core, input [12:0] addr, input [31:0] data);
        integer w;
        begin
            w = 32'(addr[RAW-1:2]);
            if (addr[12:RAW] != '0 || addr[1:0] != 2'b00)
                error($sformatf("core %0d fetched from invalid address %h", core, addr));
            else if (!golden_ok[w])
                error($sformatf("core %0d fetched %h, outside the program", core, addr));
            else if (data !== golden[w])
                error($sformatf("core %0d fetched %h from %h, image has %h",
                                core, data, addr, golden[w]));
        end
    endtask

    always @(posedge clk) begin
        if (!rst && core_run) begin
            if (dut.i0_req && dut.i0_valid) check_fetch(0, dut.i0_addr, dut.i0_rdata);
            if (dut.i1_req && dut.i1_valid) check_fetch(1, dut.i1_addr, dut.i1_rdata);
        end
    end

    reg [31:0]    shadow [0:WORDS-1];
    reg [3:0]     known  [0:WORDS-1];
    reg           rd_pending = 0;
    reg [RAW-3:0] rd_word;
    reg [2:0]     rd_src;

    initial for (int i = 0; i < WORDS; i++) known[i] = 4'b0000;

    function automatic [31:0] lane_mask(input [3:0] m);
        lane_mask = {{8{m[3]}}, {8{m[2]}}, {8{m[1]}}, {8{m[0]}}};
    endfunction

    always @(posedge clk) begin
        if (rd_pending) begin
            if ((dut.bus_rdata & lane_mask(known[rd_word]))
                    !== (shadow[rd_word] & lane_mask(known[rd_word])))
                error($sformatf("read of %h by source %0d returned %h, RAM holds %h (bytes %b known)",
                                {rd_word, 2'b00}, rd_src, dut.bus_rdata,
                                shadow[rd_word], known[rd_word]));
        end
        rd_pending <= 1'b0;
        if (!rst && dut.b_valid && !dut.b_addr[12]) begin
            if (dut.b_addr[11:RAW] != '0)
                error($sformatf("source %0d accessed %h, past the end of RAM", dut.b_src, dut.b_addr));
            else if (dut.b_wstrb == 4'b0000) begin
                rd_pending <= 1'b1;
                rd_word    <= dut.b_addr[RAW-1:2];
                rd_src     <= dut.b_src;
            end else begin
                shadow[dut.b_addr[RAW-1:2]] <=
                    (shadow[dut.b_addr[RAW-1:2]] & ~lane_mask(dut.b_wstrb))
                  | (dut.b_wdata & lane_mask(dut.b_wstrb));
                known[dut.b_addr[RAW-1:2]] <= known[dut.b_addr[RAW-1:2]] | dut.b_wstrb;
            end
        end
    end

    // A load accepted in cycle N must return RAM as it was before any write
    // granted in cycle N.
    reg [31:0] ld_exp  [2];
    reg [3:0]  ld_known[2];
    reg [12:0] ld_addr [2];
    reg        ld_pending[2] = '{1'b0, 1'b0};

    task automatic check_load(input integer c, input [31:0] got_data);
        if ((got_data & lane_mask(ld_known[c])) !== (ld_exp[c] & lane_mask(ld_known[c])))
            error($sformatf("core %0d load of %h returned %h, RAM held %h (bytes %b known)",
                            c, ld_addr[c], got_data, ld_exp[c], ld_known[c]));
    endtask

    always @(posedge clk) begin
        if (ld_pending[0]) check_load(0, dut.c0_d_rdata);
        if (ld_pending[1]) check_load(1, dut.c1_d_rdata);
        ld_pending[0] <= 1'b0;
        ld_pending[1] <= 1'b0;
        if (!rst && core_run) begin
            if (dut.c0_d_req && dut.c0_d_gnt && dut.c0_d_wstrb == 4'b0000 && !dut.c0_d_addr[12]) begin
                ld_pending[0] <= 1'b1;
                ld_addr[0]    <= dut.c0_d_addr;
                ld_exp[0]     <= shadow[dut.c0_d_addr[RAW-1:2]];
                ld_known[0]   <= known[dut.c0_d_addr[RAW-1:2]];
            end
            if (dut.c1_d_req && dut.c1_d_gnt && dut.c1_d_wstrb == 4'b0000 && !dut.c1_d_addr[12]) begin
                ld_pending[1] <= 1'b1;
                ld_addr[1]    <= dut.c1_d_addr;
                ld_exp[1]     <= shadow[dut.c1_d_addr[RAW-1:2]];
                ld_known[1]   <= known[dut.c1_d_addr[RAW-1:2]];
            end
        end
    end

    integer wait_d0 = 0, wait_d1 = 0, wait_i0 = 0, wait_i1 = 0;

    always @(posedge clk) begin
        if (!rst) begin
            if ((32'(dut.d0_gnt) + 32'(dut.d1_gnt) + 32'(dut.ic0_gnt) + 32'(dut.ic1_gnt)) > 1)
                error("more than one grant in a cycle");
            if (dut.d0_gnt  && !dut.d0_req)  error("d0 granted without a request");
            if (dut.d1_gnt  && !dut.d1_req)  error("d1 granted without a request");
            if (dut.ic0_gnt && !dut.ic0_req) error("ic0 granted without a request");
            if (dut.ic1_gnt && !dut.ic1_req) error("ic1 granted without a request");
            if (dut.loading) begin
                wait_d0 = 0; wait_d1 = 0; wait_i0 = 0; wait_i1 = 0;
            end else begin
                wait_d0 = (dut.d0_req  && !dut.d0_gnt)  ? wait_d0 + 1 : 0;
                wait_d1 = (dut.d1_req  && !dut.d1_gnt)  ? wait_d1 + 1 : 0;
                wait_i0 = (dut.ic0_req && !dut.ic0_gnt) ? wait_i0 + 1 : 0;
                wait_i1 = (dut.ic1_req && !dut.ic1_gnt) ? wait_i1 + 1 : 0;
                if (wait_d0 == 4) error("d0 waited more than 3 cycles for a grant");
                if (wait_d1 == 4) error("d1 waited more than 3 cycles for a grant");
                if (wait_i0 == 4) error("ic0 waited more than 3 cycles for a grant");
                if (wait_i1 == 4) error("ic1 waited more than 3 cycles for a grant");
            end
        end
    end

    integer retired0 = 0, retired1 = 0;
    always @(posedge clk) begin
        if (dut.c0_retire) retired0 <= retired0 + 1;
        if (dut.c1_retire) retired1 <= retired1 + 1;
    end

    string  hexfile, hexfile2, tracefile, expect_uart, uart_text;
    integer waited, i, next_image = 2;
    reg     multicore, reload, verdict;

    task automatic wait_done;
        begin
            waited = 0;
            while (!done && waited < cycle_limit) begin
                @(posedge clk);
                waited = waited + 1;
            end
            if (!done)
                error($sformatf("done never rose (exit flags %b, lock %b)", dbg_exit, dbg_lock));
        end
    endtask

    initial begin
        debug_mode = $test$plusargs("DEBUG");
        if (debug_mode) cycle_limit = 10000;
        void'($value$plusargs("CYCLES=%d", cycle_limit));
        void'($value$plusargs("TRACE_REGS=%d", trace_regs));
        void'($value$plusargs("DUMP_REGS=%d", dump_end));
        void'($value$plusargs("PRINT_UART=%d", print_uart));
        if (cycle_limit < 1) $fatal(1, "CYCLES must be positive");
        read_register_checks();
        if (!$value$plusargs("HEX=%s", hexfile)) hexfile = "tests/rv32e_test.hex";
        reload    = $value$plusargs("HEX2=%s", hexfile2);
        if (debug_mode && reload) $fatal(1, "DEBUG supports one image; use DONE for reload checks");
        multicore = $test$plusargs("DONE") || reload;
        expect_uart = "P";
        if ($test$plusargs("UART=")) begin
            expect_uart = "";
            void'($value$plusargs("UART=%s", expect_uart));
        end
        if ($value$plusargs("TRACE=%s", tracefile)) begin
            $dumpfile(tracefile);
            $dumpvars(0, soc_top_tb);
        end

        read_image(hexfile);
        repeat (10) @(posedge clk);
        @(negedge clk);
        rst = 0;
        repeat (10) @(posedge clk);
        @(negedge clk);
        load_image();

        if (debug_mode) begin
            waited = 0;
            while (!done && waited < cycle_limit) begin
                @(negedge clk);
                waited = waited + 1;
            end
            // Drain the final serial byte on EXIT, but respect a budget stop.
            if (done) repeat (12 * CPB) @(negedge clk);
            check_registers();
            $display("Stopped %s: %s, cycles=%0d retired=%0d/%0d", hexfile,
                     done ? "both cores wrote EXIT" : "cycle budget reached",
                     waited, retired0, retired1);
            if (n_errors != 0) $fatal(1, "Simulation failed: %0d checker errors", n_errors);
            $finish;
        end

        if (!multicore) begin
            waited = 0;
            while (ngot == 0 && waited < cycle_limit) begin
                @(posedge clk);
                waited = waited + 1;
            end
            check_registers();
            verdict = (ngot > 0) && (got[0] == "P") && (n_errors == 0);
            if (ngot == 0)
                $display("TIMEOUT %0s  no serial output after %0d cycles", hexfile, waited);
            else if (!verdict)
                $display("FAIL    %0s  first char '%c', %0d checker errors", hexfile, got[0], n_errors);
            else
                $display("PASS    %0s  (%0d words, %0d + %0d instructions)",
                         hexfile, nwords, retired0, retired1);
            if (!verdict) $fatal(1, "Simulation failed");
            $finish;
        end

        wait_done();
        while (reload) begin
            if (dump_end != 0) begin
                dump_registers(0, dut.c0_retire_pc);
                dump_registers(1, dut.c1_retire_pc);
            end
            read_image(hexfile2);
            load_image();
            if (done) error("done still high after the reload");
            wait_done();
            next_image = next_image + 1;
            reload = $value$plusargs($sformatf("HEX%0d=%%s", next_image), hexfile2);
        end

        waited = 0;
        while (ngot < expect_uart.len() && waited < 40 * CPB) begin
            @(posedge clk);
            waited = waited + 1;
        end
        repeat (12 * CPB) @(posedge clk);
        uart_text = "";
        for (i = 0; i < ngot && i < 64; i = i + 1)
            uart_text = {uart_text, string'(got[i])};

        if (expect_uart.len() != 0) begin
            if (ngot > 64) error("UART output exceeded the 64-byte capture buffer");
            if (uart_text != expect_uart)
                error($sformatf("UART printed \"%0s\", expected \"%0s\"", uart_text, expect_uart));
        end
        if (retired0 == 0 || retired1 == 0)
            error($sformatf("a core never ran: %0d and %0d instructions", retired0, retired1));
        check_registers();

        if (n_errors == 0)
            $display("PASS    %0s%0s  (UART \"%0s\", %0d + %0d instructions)",
                     hexfile, next_image > 2 ? " then reload" : "", uart_text, retired0, retired1);
        else begin
            $display("FAIL    %0s  %0d checker errors", hexfile, n_errors);
            $fatal(1, "Simulation failed");
        end
        $finish;
    end
endmodule
