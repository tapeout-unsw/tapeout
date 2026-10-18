`timescale 1ns / 1ps
//
// Test harness, not RTL: the bus-to-core path for both cores.
//
//   core0.d ─┐
//   core1.d ─┤
//   ic0 (py)─┼─> arbiter ─> addr_decode ─┬─> sram_mem (real, RAW byte-address bits)
//   ic1 (py)─┤                           └─> mmio (real) ─> uart_tx (real)
//   boot ────┘                     bus_rdata ──> d_rdata of BOTH cores
//     └─ use_bl = 0: driven from Python (also used to preload / read back RAM)
//        use_bl = 1: the real bootloader, fed bytes on rx_data / rx_valid
//
// Real RTL: core x2, arbiter, addr_decode, sram_mem, bootloader, mmio, uart_tx.
// Models:
//   * Each core fetches from its own ROM, not through the I-cache (that's the
//     core-to-cache test), so only the data port goes over the bus.
//     i_valid = i_req & i_go*, so the test can stall fetches at random and
//     shift how the two cores' accesses line up.
//   * The I-cache refill ports ic0/ic1 are driven from Python for contention.
//
// Core reset = rst | core_hold | (use_bl & ~core_run), as the SoC holds the
// cores until the bootloader is done.
//
module core_bus_harness #(
    parameter int RAW      = 11,        // 2 KiB RAM; must match the SoC's addr_decode RAW
    parameter int UART_CPB = 4          // UART clock cycles per bit (fast, for simulation)
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        core_hold,      // keep both cores in reset

    // instruction-fetch throttles, one per core
    input  logic        i_go0,
    input  logic        i_go1,

    // bootloader port when use_bl = 0 (driven by Python)
    input  logic        use_bl,
    input  logic        loading,
    input  logic        boot_req,
    input  logic [12:0] boot_addr,
    input  logic [31:0] boot_wdata,
    input  logic [3:0]  boot_wstrb,

    // byte stream into the real bootloader (use_bl = 1)
    input  logic [7:0]  rx_data,
    input  logic        rx_valid,
    output logic        core_run,

    // I-cache refill ports (driven by Python)
    input  logic        ic0_req,
    input  logic [12:0] ic0_addr,
    output logic        ic0_gnt,
    input  logic        ic1_req,
    input  logic [12:0] ic1_addr,
    output logic        ic1_gnt,

    // ROM backdoor
    input  logic        rom_we,
    input  logic        rom_sel,        // 0 = core 0, 1 = core 1
    input  logic [7:0]  rom_waddr,      // word address
    input  logic [31:0] rom_wdata,

    // MMIO outputs
    output logic        uart_tx,
    output logic        done,           // both cores have written EXIT

    // observability
    output logic        retire0,
    output logic [31:0] retire_pc0,
    output logic        retire1,
    output logic [31:0] retire_pc1
);
    // ---------------- bootloader + boot-port mux ----------------
    logic        bl_req, bl_loading;
    logic [12:0] bl_addr;
    logic [31:0] bl_wdata;

    bootloader u_boot (
        .clk(clk), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .boot_req(bl_req), .boot_addr(bl_addr), .boot_wdata(bl_wdata),
        .core_run(core_run), .loading(bl_loading)
    );

    // what the arbiter actually sees (the test's monitor reads these)
    logic        arb_loading, arb_boot_req;
    logic [12:0] arb_boot_addr;
    logic [31:0] arb_boot_wdata;
    logic [3:0]  arb_boot_wstrb;
    assign arb_loading    = use_bl ? bl_loading : loading;
    assign arb_boot_req   = use_bl ? bl_req     : boot_req;
    assign arb_boot_addr  = use_bl ? bl_addr    : boot_addr;
    assign arb_boot_wdata = use_bl ? bl_wdata   : boot_wdata;
    assign arb_boot_wstrb = use_bl ? 4'hF       : boot_wstrb;

    logic core_rst;
    assign core_rst = rst | core_hold | (use_bl & ~core_run);

    // ---------------- instruction ROMs ----------------
    logic [31:0] rom0 [0:255];
    logic [31:0] rom1 [0:255];
    always_ff @(posedge clk)
        if (rom_we) begin
            if (rom_sel) rom1[rom_waddr] <= rom_wdata;
            else         rom0[rom_waddr] <= rom_wdata;
        end

    logic        i_req0, i_req1, i_valid0, i_valid1;
    /* verilator lint_off UNUSEDSIGNAL */   // ROM only decodes i_addr[9:2]
    logic [12:0] i_addr0, i_addr1;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [31:0] i_rdata0, i_rdata1;
    assign i_valid0 = i_req0 & i_go0;
    assign i_valid1 = i_req1 & i_go1;
    assign i_rdata0 = rom0[i_addr0[9:2]];
    assign i_rdata1 = rom1[i_addr1[9:2]];

    // ---------------- cores ----------------
    logic        d0_req, d1_req, d0_gnt, d1_gnt;
    logic [12:0] d0_addr, d1_addr;
    logic [31:0] d0_wdata, d1_wdata;
    logic [3:0]  d0_wstrb, d1_wstrb;
    logic [31:0] bus_rdata;

    core #(.NREGS(16)) u_core0 (
        .clk(clk), .rst(core_rst),
        .i_req(i_req0), .i_addr(i_addr0), .i_valid(i_valid0), .i_rdata(i_rdata0),
        .d_req(d0_req), .d_addr(d0_addr), .d_wdata(d0_wdata), .d_wstrb(d0_wstrb),
        .d_gnt(d0_gnt), .d_rdata(bus_rdata),
        .retire(retire0), .retire_pc(retire_pc0)
    );

    core #(.NREGS(16)) u_core1 (
        .clk(clk), .rst(core_rst),
        .i_req(i_req1), .i_addr(i_addr1), .i_valid(i_valid1), .i_rdata(i_rdata1),
        .d_req(d1_req), .d_addr(d1_addr), .d_wdata(d1_wdata), .d_wstrb(d1_wstrb),
        .d_gnt(d1_gnt), .d_rdata(bus_rdata),
        .retire(retire1), .retire_pc(retire_pc1)
    );

    // ---------------- arbiter ----------------
    logic        b_valid;
    logic [12:0] b_addr;
    logic [31:0] b_wdata;
    logic [3:0]  b_wstrb;
    logic [2:0]  b_src;

    arbiter u_arb (
        .clk(clk), .rst(rst), .loading(arb_loading),
        .boot_req(arb_boot_req), .boot_addr(arb_boot_addr),
        .boot_wdata(arb_boot_wdata), .boot_wstrb(arb_boot_wstrb),
        .d0_req(d0_req), .d0_addr(d0_addr), .d0_wdata(d0_wdata), .d0_wstrb(d0_wstrb), .d0_gnt(d0_gnt),
        .d1_req(d1_req), .d1_addr(d1_addr), .d1_wdata(d1_wdata), .d1_wstrb(d1_wstrb), .d1_gnt(d1_gnt),
        .ic0_req(ic0_req), .ic0_addr(ic0_addr), .ic0_wdata(32'd0), .ic0_wstrb(4'd0), .ic0_gnt(ic0_gnt),
        .ic1_req(ic1_req), .ic1_addr(ic1_addr), .ic1_wdata(32'd0), .ic1_wstrb(4'd0), .ic1_gnt(ic1_gnt),
        .b_valid(b_valid), .b_addr(b_addr), .b_wdata(b_wdata), .b_wstrb(b_wstrb), .b_src(b_src)
    );

    // ---------------- address decode ----------------
    logic           s_en, m_en, m_we, m_core;
    logic [RAW-3:0] s_addr;
    logic [31:0]    s_wdata, s_rdata, m_rdata;
    logic [3:0]     s_wstrb;
    logic [2:0]     m_addr;
    logic [7:0]     m_wdata;

/*     addr_decode #(.RAW(RAW)) u_dec (
        .clk(clk),
        .b_valid(b_valid), .b_addr(b_addr), .b_wdata(b_wdata), .b_wstrb(b_wstrb), .b_src(b_src),
        .s_en(s_en), .s_addr(s_addr), .s_wdata(s_wdata), .s_wstrb(s_wstrb), .s_rdata(s_rdata),
        .m_en(m_en), .m_addr(m_addr), .m_we(m_we), .m_wdata(m_wdata), .m_core(m_core), .m_rdata(m_rdata),
        .bus_rdata(bus_rdata)
    ); */

    // Temporary simulation model of address decoding.
    // Replace with the real addr_decode instance once implemented.

    logic model_sel_q;

    assign s_en    = b_valid & ~b_addr[12];
    assign s_addr  = b_addr[RAW-1:2];
    assign s_wdata = b_wdata;
    assign s_wstrb = b_wstrb;

    assign m_en    = b_valid & b_addr[12];
    assign m_addr  = b_addr[4:2];
    assign m_we    = |b_wstrb;
    assign m_wdata = b_wdata[7:0];
    assign m_core  = (b_src == 3'd2);

    // Select the response belonging to the preceding clock's access.
    always_ff @(posedge clk)
        model_sel_q <= b_addr[12];

    assign bus_rdata = model_sel_q ? m_rdata : s_rdata;



    // ---------------- SRAM: the real controller + macros ----------------
    sram_mem #(.WORDS(1 << (RAW - 2))) u_sram (
        .clk(clk), .en(s_en), .addr(s_addr),
        .wdata(s_wdata), .wstrb(s_wstrb), .rdata(s_rdata)
    );

    // ---------------- MMIO (real) + UART transmit (real, fast baud) ----------------
    // mmio clears LOCK / EXIT while core_run is low, so give it the same
    // "cores are running" signal the cores see.
    logic       tx_start, tx_busy;
    logic [7:0] tx_data;
    logic       mmio_run;
    assign mmio_run = use_bl ? core_run : ~core_hold;

    /* verilator lint_off PINCONNECTEMPTY */
    mmio u_mmio (
        .clk(clk), .rst(rst), .core_run(mmio_run),
        .m_en(m_en), .m_addr(m_addr), .m_we(m_we), .m_wdata(m_wdata), .m_core(m_core),
        .m_rdata(m_rdata),
        .tx_start(tx_start), .tx_data(tx_data), .tx_busy(tx_busy),
        .done(done), .dbg_lock(), .dbg_exit()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    uart_tx #(.CLK_FREQ(UART_CPB), .BAUD_RATE(1)) u_uart_tx (
        .clk(clk), .rst(rst),
        .tx_start(tx_start), .tx_data(tx_data),
        .tx(uart_tx), .tx_busy(tx_busy)
    );
endmodule
