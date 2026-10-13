`timescale 1ns/1ps

module test_tb;
    localparam int WORDS = 1024;

    logic clk = 0;
    logic rst = 1;
    always #5 clk = ~clk;

    logic [31:0] rom [0:WORDS-1];
    string hexfile;

    // Core interfaces
    wire i_req0, i_req1, d_req0, d_req1;
    wire [12:0] i_addr0, i_addr1;
    wire [12:0] d_addr0, d_addr1;
    wire [31:0] d_wdata0, d_wdata1;
    wire [3:0] d_wstrb0, d_wstrb1;

    logic i_valid0 = 0, i_valid1 = 0;
    logic [31:0] i_rdata0 = 0, i_rdata1 = 0;

    wire d_gnt0, d_gnt1;
    wire [31:0] d_rdata0, d_rdata1;
    wire retire0, retire1;
    wire [31:0] retire_pc0, retire_pc1;

    // Two RV32E cores
    core #(.NREGS(16)) u_core0 (
        .clk(clk), .rst(rst),
        .i_req(i_req0), .i_addr(i_addr0),
        .i_valid(i_valid0), .i_rdata(i_rdata0),
        .d_req(d_req0), .d_addr(d_addr0),
        .d_wdata(d_wdata0), .d_wstrb(d_wstrb0),
        .d_gnt(d_gnt0), .d_rdata(d_rdata0),
        .retire(retire0), .retire_pc(retire_pc0)
    );

    core #(.NREGS(16)) u_core1 (
        .clk(clk), .rst(rst),
        .i_req(i_req1), .i_addr(i_addr1),
        .i_valid(i_valid1), .i_rdata(i_rdata1),
        .d_req(d_req1), .d_addr(d_addr1),
        .d_wdata(d_wdata1), .d_wstrb(d_wstrb1),
        .d_gnt(d_gnt1), .d_rdata(d_rdata1),
        .retire(retire1), .retire_pc(retire_pc1)
    );

    // Shared ROM, with one-cycle read latency
    always @(posedge clk) begin
        if (rst) begin
            i_valid0 <= 0;
            i_valid1 <= 0;
        end else begin
            i_valid0 <= i_req0;
            i_valid1 <= i_req1;

            if (i_req0)
                i_rdata0 <= rom[i_addr0[11:2]];

            if (i_req1)
                i_rdata1 <= rom[i_addr1[11:2]];
        end
    end

    // Shared MMIO bus: Core 0 gets priority
    assign d_gnt0 = d_req0;
    assign d_gnt1 = d_req1 && !d_req0;

    wire m_en = d_gnt0 || d_gnt1;
    wire m_core = d_gnt1;

    wire [12:0] m_addr =
        d_gnt0 ? d_addr0 : d_addr1;

    wire [3:0] m_strb =
        d_gnt0 ? d_wstrb0 : d_wstrb1;

    wire [31:0] m_wdata =
        d_gnt0 ? d_wdata0 : d_wdata1;

    wire [31:0] m_rdata;

    // Actual MMIO module from rtl/periph/mmio.sv
    mmio u_mmio (
        .clk(clk),
        .rst(rst),
        .core_run(1'b1),
        .m_en(m_en),
        .m_addr(m_addr[4:2]),
        .m_we(|m_strb),
        .m_wdata(m_wdata[7:0]),
        .m_core(m_core),
        .m_rdata(m_rdata),
        .tx_start(),
        .tx_data(),
        .tx_busy(1'b0),
        .done(),
        .dbg_lock(),
        .dbg_exit()
    );

    // Data arrives in the cycle after a grant
    assign d_rdata0 = m_rdata;
    assign d_rdata1 = m_rdata;

    // Check that only CORE_ID is accessed
    always @(posedge clk) if (!rst) begin
        if (d_gnt0 &&
            (d_addr0 != 13'h100C || d_wstrb0 != 0))
            $fatal(1, "Core 0 unexpected access: %h",
                   d_addr0);

        if (d_gnt1 &&
            (d_addr1 != 13'h100C || d_wstrb1 != 0))
            $fatal(1, "Core 1 unexpected access: %h",
                   d_addr1);
    end

    // Check that each core stays in its correct loop
    integer hits0 = 0;
    integer hits1 = 0;

    always @(posedge clk) if (!rst) begin
        if (retire0) begin
            if (retire_pc0 >= 32'h10 &&
                retire_pc0 != 32'h18 &&
                retire_pc0 != 32'h1C)
                $fatal(1, "Core 0 wrong PC: %h",
                       retire_pc0);

            if (retire_pc0 == 32'h18)
                hits0 <= hits0 + 1;
        end

        if (retire1) begin
            if (retire_pc1 >= 32'h10 &&
                retire_pc1 != 32'h10 &&
                retire_pc1 != 32'h14)
                $fatal(1, "Core 1 wrong PC: %h",
                       retire_pc1);

            if (retire_pc1 == 32'h10)
                hits1 <= hits1 + 1;
        end
    end

    // Load program and run test
    initial begin
        if (!$value$plusargs("HEX=%s", hexfile))
            hexfile = "programs/hex/test.hex";

        for (int i = 0; i < WORDS; i++)
            rom[i] = 32'h0;

        $readmemh(hexfile, rom);

        repeat (4) @(negedge clk);
        rst = 0;

        for (int cycle = 0; cycle < 250; cycle++) begin
            @(negedge clk);

            if (hits0 >= 3 && hits1 >= 3) begin
                if (u_core0.u_rf.regs[5] !== 32'h000FF000)
                    $fatal(1, "Core 0 x5 incorrect");

                if (u_core1.u_rf.regs[5] !== 32'h0FF00000)
                    $fatal(1, "Core 1 x5 incorrect");

                $display(
                    "PASS test: Core 0 and Core 1 correct"
                );
                $finish;
            end
        end

        $fatal(1, "TIMEOUT: core0=%0d core1=%0d",
               hits0, hits1);
    end
endmodule
