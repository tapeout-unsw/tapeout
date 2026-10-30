`timescale 1ns/1ps
//
// Testbench: runs a hex image on core and reports PASS / FAIL / TIMEOUT.
//
// Select the image with the plusarg +HEX=<path>. Select the ISA with the
// NREGS parameter (16 = RV32E, 32 = RV32I).
//
// Both core ports are served from one memory. Each request is granted after a
// random delay of 0 to MAXDELAY cycles (plusarg +MAXDELAY=<n>, default 3), and
// read data arrives the cycle after the grant, as on the real bus.
//
// +FETCHTRACE=<file> writes the address of every instruction fetch, one hex
// value per line, for replay in icache_tb.
//
// Memory map
//   0x0000 - 0x0FFF   RAM, 1024 words, synchronous read
//   0x1000            UART data   (write: transmit the low byte)
//   0x1004            UART status (read: bit 0 = busy, always 0 here)
//
// The test environment prints 'P' on pass, or 'F' followed by two hex digits of
// TESTNUM on failure, then halts. This bench watches the memory port for those
// writes.
//
module core_mc_tb;

    parameter integer NREGS   = 16;
    parameter integer TIMEOUT = 400000;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    wire        i_req, d_req, retire;
    wire [12:0] i_addr, d_addr;
    wire [31:0] d_wdata, retire_pc;
    wire [3:0]  d_wstrb;
    wire        i_valid, d_gnt;
    reg  [31:0] i_rdata, d_rdata;
    wire        d_atomic;              // high while an amoswap holds the bus; one core here, so nothing to hold

    core #(.NREGS(NREGS)) dut (
        .clk(clk), .rst(rst),
        .i_req(i_req), .i_addr(i_addr), .i_valid(i_valid), .i_rdata(i_rdata),
        .d_req(d_req), .d_addr(d_addr), .d_wdata(d_wdata), .d_wstrb(d_wstrb),
        .d_gnt(d_gnt), .d_rdata(d_rdata),
        .retire(retire), .retire_pc(retire_pc), .d_atomic(d_atomic)
    );

    // ---------------- unified memory ----------------
    localparam integer WORDS = 1024;
    reg [31:0] ram [0:WORDS-1];

    integer maxdelay;
    integer i_delay = 0, d_delay = 0;
    reg     i_wait = 0;                  // grant given, i_valid this cycle

    // Instruction port: one grant per fetch, i_valid the cycle after.
    wire i_gnt = i_req && !i_wait && (i_delay == 0);
    assign i_valid = i_wait;

    always @(posedge clk) begin
        if (rst) begin
            i_wait  <= 0;
            i_delay <= 0;
        end else if (i_wait) begin
            i_wait  <= 0;
            i_delay <= $urandom_range(0, maxdelay);
        end else if (i_gnt) begin
            i_wait  <= 1;
            i_rdata <= ram[i_addr[11:2]];
        end else if (i_req) begin
            i_delay <= i_delay - 1;
        end
    end

    // Data port: d_gnt accepts the access, read data the cycle after.
    wire        sel_ram  = !d_addr[12];
    wire        sel_uart =  d_addr[12];
    wire [9:0]  widx     = d_addr[11:2];
    reg  [31:0] wmask;
    assign d_gnt = d_req && (d_delay == 0);

    always @(posedge clk) begin
        if (rst) d_delay <= 0;
        else if (d_gnt) d_delay <= $urandom_range(0, maxdelay);
        else if (d_req) d_delay <= d_delay - 1;

        if (d_gnt && sel_ram) begin
            // Byte strobes as a mask (avoids part-select on a variable index).
            wmask = {{8{d_wstrb[3]}}, {8{d_wstrb[2]}},
                     {8{d_wstrb[1]}}, {8{d_wstrb[0]}}};
            ram[widx] <= (ram[widx] & ~wmask) | (d_wdata & wmask);
            d_rdata   <= ram[widx];
        end else if (d_gnt && sel_uart) begin
            d_rdata <= 32'd0;            // UART status: never busy
        end
    end

    // ---------------- UART monitor ----------------
    reg        done   = 0;
    reg        failed = 0;
    reg [7:0]  c0 = 8'h00, c1 = 8'h00, c2 = 8'h00;
    integer    nchar  = 0;

    always @(posedge clk) begin
        if (!rst && d_gnt && sel_uart && (d_wstrb != 4'b0000)
                 && (d_addr[11:0] == 12'h000)) begin
            if      (nchar == 0) c0 = d_wdata[7:0];
            else if (nchar == 1) c1 = d_wdata[7:0];
            else if (nchar == 2) c2 = d_wdata[7:0];
            nchar = nchar + 1;
            if (d_wdata[7:0] == "P") done = 1;
            if (c0 == "F" && nchar >= 3) begin done = 1; failed = 1; end
        end
    end

    // ---------------- fetch trace ----------------
    integer fetch_fd = 0;
    string  fetchfile;
    initial if ($value$plusargs("FETCHTRACE=%s", fetchfile)) fetch_fd = $fopen(fetchfile, "w");
    always @(posedge clk)
        if (fetch_fd != 0 && !rst && i_valid) begin
            $fwrite(fetch_fd, "%h\n", i_addr);
            $fflush(fetch_fd);
        end

    // ---------------- run ----------------
    integer      cycles = 0;
    integer      i;
    string hexfile;
    string tracefile;
    integer image_fd;

    initial begin
        if (!$value$plusargs("MAXDELAY=%d", maxdelay))
            maxdelay = 3;
        if (!$value$plusargs("HEX=%s", hexfile))
            hexfile = "tests/addi.hex";
        image_fd = $fopen(hexfile, "r");
        if (image_fd == 0) $fatal(1, "Cannot open image: %s", hexfile);
        $fclose(image_fd);
        if ($value$plusargs("TRACE=%s", tracefile)) begin
            $dumpfile(tracefile);
            $dumpvars(0, core_mc_tb);
        end
        for (i = 0; i < WORDS; i = i + 1) ram[i] = 32'h0;
        $readmemh(hexfile, ram);

        repeat (4) @(posedge clk);
        @(negedge clk);
        rst = 0;

        while (!done && cycles < TIMEOUT) begin
            @(posedge clk);
            cycles = cycles + 1;
        end

        if (!done)
            $display("TIMEOUT %0s  after %0d cycles (pc=%08x)", hexfile, cycles, retire_pc);
        else if (failed)
            $display("FAIL    %0s  testnum=%c%c", hexfile, c1, c2);
        else
            $display("PASS    %0s  (%0d cycles)", hexfile, cycles);
        if (!done || failed) $fatal(1, "Simulation failed");
        $finish;
    end
endmodule
