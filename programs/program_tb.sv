`timescale 1ns/1ps

module program_tb;
    /* verilator lint_off BLKSEQ */
    parameter int NREGS = 16;
    localparam int WORDS = 256;
    logic clk, rst = 1;
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    logic [31:0] ram [0:WORDS-1];
    wire [1:0] i_req, d_req, d_gnt, retire;
    wire [12:0] i_addr [2], d_addr [2];
    wire [31:0] d_wdata [2], retire_pc [2];
    wire [3:0] d_wstrb [2];
    logic [1:0] i_valid;
    logic [31:0] i_rdata [2];
    wire [31:0] bus_rdata;

    core #(.NREGS(NREGS)) core0 (
        .clk(clk), .rst(rst),
        .i_req(i_req[0]), .i_addr(i_addr[0]), .i_valid(i_valid[0]), .i_rdata(i_rdata[0]),
        .d_req(d_req[0]), .d_addr(d_addr[0]), .d_wdata(d_wdata[0]), .d_wstrb(d_wstrb[0]),
        .d_gnt(d_gnt[0]), .d_rdata(bus_rdata), .retire(retire[0]), .retire_pc(retire_pc[0])
    );
    core #(.NREGS(NREGS)) core1 (
        .clk(clk), .rst(rst),
        .i_req(i_req[1]), .i_addr(i_addr[1]), .i_valid(i_valid[1]), .i_rdata(i_rdata[1]),
        .d_req(d_req[1]), .d_addr(d_addr[1]), .d_wdata(d_wdata[1]), .d_wstrb(d_wstrb[1]),
        .d_gnt(d_gnt[1]), .d_rdata(bus_rdata), .retire(retire[1]), .retire_pc(retire_pc[1])
    );

    string hexfile;
    integer cycle_limit = 10000, trace_regs = 0, dump_end = 1;
    integer retired [2];
    wire b_valid;
    wire [12:0] b_addr;
    wire [31:0] b_wdata;
    wire [3:0] b_wstrb;
    wire [2:0] b_src;
    wire unused_ic0_gnt, unused_ic1_gnt;
    arbiter u_arbiter (
        .clk(clk), .rst(rst), .loading(1'b0),
        .boot_req(1'b0), .boot_addr(13'd0), .boot_wdata(32'd0), .boot_wstrb(4'd0),
        .d0_req(d_req[0] && !rst), .d0_addr(d_addr[0]),
        .d0_wdata(d_wdata[0]), .d0_wstrb(d_wstrb[0]), .d0_gnt(d_gnt[0]),
        .d1_req(d_req[1] && !rst), .d1_addr(d_addr[1]),
        .d1_wdata(d_wdata[1]), .d1_wstrb(d_wstrb[1]), .d1_gnt(d_gnt[1]),
        .ic0_req(1'b0), .ic0_addr(13'd0), .ic0_wdata(32'd0), .ic0_wstrb(4'd0),
        .ic1_req(1'b0), .ic1_addr(13'd0), .ic1_wdata(32'd0), .ic1_wstrb(4'd0),
        .ic0_gnt(unused_ic0_gnt), .ic1_gnt(unused_ic1_gnt),
        .b_valid(b_valid), .b_addr(b_addr), .b_wdata(b_wdata),
        .b_wstrb(b_wstrb), .b_src(b_src)
    );

    wire m_en = b_valid && b_addr[12];
    wire [31:0] m_rdata;
    logic [31:0] ram_rdata;
    logic read_mmio;
    wire done;
    wire unused_dbg_lock;
    wire [1:0] unused_dbg_exit;
    wire tx_start;
    wire [7:0] tx_data;
    mmio u_mmio (
        .clk(clk), .rst(rst), .core_run(!rst),
        .m_en(m_en), .m_addr(b_addr[4:2]), .m_we(|b_wstrb),
        .m_wdata(b_wdata[7:0]), .m_core(b_src == 3'd2), .m_rdata(m_rdata),
        .tx_start(tx_start), .tx_data(tx_data), .tx_busy(1'b0),
        .done(done), .dbg_lock(unused_dbg_lock), .dbg_exit(unused_dbg_exit)
    );
    assign bus_rdata = read_mmio ? m_rdata : ram_rdata;

    // Respond once per fetch
    always @(posedge clk) begin
        if (rst) begin
            i_valid <= 0;
            read_mmio <= 0;
            retired[0] = 0;
            retired[1] = 0;
        end else begin
            for (int c = 0; c < 2; c++) begin
                i_valid[c] <= 0;
                if (i_req[c] && !i_valid[c]) begin
                    if (i_addr[c] >= 13'h400 || i_addr[c][1:0] != 0)
                        $fatal(1, "Invalid instruction address core=%0d addr=%h", c, i_addr[c]);
                    i_valid[c] <= 1;
                    i_rdata[c] <= ram[i_addr[c][9:2]];
                end
                if (retire[c]) retired[c]++;
            end
            if (b_valid) begin
                read_mmio <= b_addr[12];
                if (!b_addr[12]) begin
                    if (b_addr >= 13'h400) $fatal(1, "RAM access exceeds 1 KiB: %h", b_addr);
                    ram_rdata <= ram[b_addr[9:2]];
                    for (int lane = 0; lane < 4; lane++)
                        if (b_wstrb[lane])
                            ram[b_addr[9:2]][8*lane +: 8] <= b_wdata[8*lane +: 8];
                end
            end
            if (tx_start) $write("%c", tx_data);
        end
    end

    task automatic dump_registers(input int c, input logic [31:0] pc);
        $display("REGS core=%0d pc=%08x", c, pc);
        for (int r = 0; r < NREGS; r++) begin
            if (c == 0) $write(" x%0d=%08x", r, r == 0 ? 32'd0 : core0.u_rf.regs[r]);
            else        $write(" x%0d=%08x", r, r == 0 ? 32'd0 : core1.u_rf.regs[r]);
            if (r % 4 == 3) $write("\n");
        end
    endtask

    // Capture the retiring PC and print after writeback
    always @(posedge clk) begin : trace_step
        logic [1:0] completed;
        logic [31:0] pc0, pc1;
        completed = retire & {2{!rst}};
        pc0 = retire_pc[0];
        pc1 = retire_pc[1];
        #1;
        if (trace_regs != 0) begin
            if (completed[0]) dump_registers(0, pc0);
            if (completed[1]) dump_registers(1, pc1);
        end
    end

    initial begin : run_program
        integer image_fd;
        integer cycles;
        if (!$value$plusargs("HEX=%s", hexfile)) hexfile = "programs/hex/test.hex";
        void'($value$plusargs("CYCLES=%d", cycle_limit));
        void'($value$plusargs("TRACE_REGS=%d", trace_regs));
        void'($value$plusargs("DUMP_REGS=%d", dump_end));
        if (cycle_limit < 1) $fatal(1, "CYCLES must be positive");
        image_fd = $fopen(hexfile, "r");
        if (image_fd == 0) $fatal(1, "Cannot open image: %s", hexfile);
        $fclose(image_fd);
        for (int w = 0; w < WORDS; w++) ram[w] = 0;
        $readmemh(hexfile, ram);
        repeat (4) @(negedge clk);
        rst = 0;
        cycles = 0;
        while (!done && cycles < cycle_limit) begin
            @(negedge clk);
            cycles++;
        end
        if (dump_end != 0) begin
            dump_registers(0, retire_pc[0]);
            dump_registers(1, retire_pc[1]);
        end
        $display("Stopped %s: %s, cycles=%0d retired=%0d/%0d", hexfile,
                 done ? "both cores wrote EXIT" : "cycle budget reached", cycles, retired[0], retired[1]);
        $finish;
    end
endmodule
