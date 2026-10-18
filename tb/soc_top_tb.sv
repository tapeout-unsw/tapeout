`timescale 1ns/1ps
//
// Dual-core soc_top testbench. Loads +HEX over the serial pin and runs it on
// both cores. Every cycle it checks fetched instructions against the image,
// RAM reads against a shadow copy, and arbiter grants.
//
// Without +DONE the first UART byte must be 'P' (riscv-tests). With +DONE both
// cores must write EXIT and the UART text must equal +UART (default "P").
// +HEX2 loads a second image after the first finishes, without a reset.
//
module soc_top_tb;

    parameter integer NREGS     = 16;
    localparam integer RAW      = 11;
    localparam integer WORDS    = 1 << (RAW - 2);
    parameter integer CLK_FREQ  = 2_000_000;
    parameter integer BAUD_RATE = 100_000;
    localparam integer CPB      = CLK_FREQ / BAUD_RATE;
    parameter integer TIMEOUT   = 3_000_000;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg  rx_pin = 1'b1;
    wire tx_pin, core_run, done, dbg_lock;
    wire [1:0] dbg_exit;

    soc_top #(.NREGS(NREGS), .RAW(RAW),
              .CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) dut (
        .clk(clk), .rst(rst),
        .uart_rx(rx_pin), .uart_tx(tx_pin), .done(done),
        .dbg_core_run(core_run), .dbg_lock(dbg_lock), .dbg_exit(dbg_exit)
    );

    integer cycle = 0;
    always @(posedge clk) cycle <= cycle + 1;

    integer n_errors = 0;
    task automatic error(input string what);
        begin
            n_errors = n_errors + 1;
            if (n_errors <= 10) $display("ERROR   cycle %0d: %0s", cycle, what);
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
    integer waited, i;
    reg     multicore, reload, verdict;

    task automatic wait_done;
        begin
            waited = 0;
            while (!done && waited < TIMEOUT) begin
                @(posedge clk);
                waited = waited + 1;
            end
            if (!done)
                error($sformatf("done never rose (exit flags %b, lock %b)", dbg_exit, dbg_lock));
        end
    endtask

    initial begin
        if (!$value$plusargs("HEX=%s", hexfile)) hexfile = "tests/rv32e_test.hex";
        reload    = $value$plusargs("HEX2=%s", hexfile2);
        multicore = $test$plusargs("DONE") || reload;
        if (!$value$plusargs("UART=%s", expect_uart)) expect_uart = "P";
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

        if (!multicore) begin
            waited = 0;
            while (ngot == 0 && waited < TIMEOUT) begin
                @(posedge clk);
                waited = waited + 1;
            end
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
        if (reload) begin
            read_image(hexfile2);
            load_image();
            if (done) error("done still high after the reload");
            wait_done();
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

        if (uart_text != expect_uart)
            error($sformatf("UART printed \"%0s\", expected \"%0s\"", uart_text, expect_uart));
        if (retired0 == 0 || retired1 == 0)
            error($sformatf("a core never ran: %0d and %0d instructions", retired0, retired1));

        if (n_errors == 0)
            $display("PASS    %0s%0s  (UART \"%0s\", %0d + %0d instructions)",
                     hexfile, reload ? " then reload" : "", uart_text, retired0, retired1);
        else begin
            $display("FAIL    %0s  %0d checker errors", hexfile, n_errors);
            $fatal(1, "Simulation failed");
        end
        $finish;
    end
endmodule
