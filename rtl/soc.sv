`timescale 1ns / 1ps
//
// Single-core SoC: core, unified memory, bootloader and memory-mapped UART.
// Full-system reference for the core; the dual-core chip is soc_top.
//
// Parameters
//   NREGS      16 = RV32E (default), 32 = RV32I.
//   MEM_WORDS  depth of the unified instruction/data memory, in 32-bit words.
//   CLK_FREQ   system clock in Hz, used to derive the UART baud divider.
//   BAUD_RATE  UART baud rate.
//
// Memory map
//   0x0000 .. MEM_WORDS*4-1   RAM, synchronous read
//   0x1000                    UART data   (write: transmit the low byte)
//   0x1004                    UART status (read: bit 0 = transmitter busy)
//
// Boot sequence
//   Memory contents are undefined at power-up. The bootloader owns the memory
//   port and holds the core in reset until a program has been received over
//   serial, then asserts core_run and releases it.
//
//   Host protocol, little-endian: 4 bytes of word count N, then N*4 bytes of
//   program. Sending a further 4 bytes restarts the load and re-resets the core.
//
// Memory arbitration
//   Two requesters: the bootloader while loading, the core afterwards. They are
//   mutually exclusive by construction, since the core is held in reset until
//   loading completes. The core's two ports are never active in the same
//   cycle, so both are granted immediately.
//
module soc #(
    parameter int NREGS     = 16,
    parameter int MEM_WORDS = 1024,
    parameter int CLK_FREQ  = 100_000_000,
    parameter int BAUD_RATE = 115_200
) (
    input  logic clk,
    input  logic rst,        // power-on reset, resets the bootloader too
    input  logic uart_rx_pin,
    output logic uart_tx_pin,

    // observability
    output logic core_run,
    output logic retire,
    output logic [31:0] retire_pc
);
    localparam int AW = $clog2(MEM_WORDS);

    // ---------------- serial receive and bootloader ----------------
    logic [7:0] rx_data;
    logic       rx_valid;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_rx (
        .clk(clk), .rst(rst), .rx(uart_rx_pin),
        .rx_data(rx_data), .rx_valid(rx_valid)
    );

    logic        boot_we, loading;
    logic [12:0] boot_addr;
    logic [31:0] boot_waddr, boot_wdata;

    bootloader u_boot (
        .clk(clk), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .boot_req(boot_we), .boot_addr(boot_addr), .boot_wdata(boot_wdata),
        .core_run(core_run), .loading(loading)
    );

    assign boot_waddr = {19'd0, boot_addr};

    // ---------------- core ----------------
    logic        i_req, i_valid, d_req, d_gnt;
    logic [12:0] i_addr, d_addr;
    logic [31:0] d_wdata;
    logic [3:0]  d_wstrb;
    logic [31:0] mem_rdata;

    core #(.NREGS(NREGS)) u_core (
        .clk(clk),
        .rst(~core_run),                 // held in reset until the program lands
        .i_req(i_req), .i_addr(i_addr), .i_valid(i_valid), .i_rdata(mem_rdata),
        .d_req(d_req), .d_addr(d_addr), .d_wdata(d_wdata), .d_wstrb(d_wstrb),
        .d_gnt(d_gnt), .d_rdata(mem_rdata),
        .retire(retire), .retire_pc(retire_pc),
        .d_atomic()                      // one core, no arbiter: nothing to hold the bus against
    );

    // Fetch: issue the read once, return the word the next cycle.
    logic i_issue, i_wait;
    assign i_issue = i_req & ~i_wait;
    assign i_valid = i_wait;
    always_ff @(posedge clk) i_wait <= ~core_run ? 1'b0 : i_issue;

    assign d_gnt = d_req;

    logic        core_en;
    logic [31:0] core_addr, core_wdata;
    logic [3:0]  core_wstrb;
    assign core_en    = i_issue | d_req;
    assign core_addr  = {19'd0, d_req ? d_addr : i_addr};
    assign core_wdata = d_wdata;
    assign core_wstrb = d_req ? d_wstrb : 4'b0000;

    // ---------------- request mux ----------------
    logic        req_en;
    logic [31:0] req_addr, req_wdata;
    logic [3:0]  req_wstrb;

    always_comb begin
        if (loading) begin
            req_en    = boot_we;
            req_addr  = boot_waddr;
            req_wdata = boot_wdata;
            req_wstrb = 4'b1111;
        end else begin
            req_en    = core_en;
            req_addr  = core_addr;
            req_wdata = core_wdata;
            req_wstrb = core_wstrb;
        end
    end

    // ---------------- address decode ----------------
    logic sel_ram, sel_uart;
    assign sel_ram  = (req_addr < (MEM_WORDS*4));
    assign sel_uart = (req_addr >= 32'h1000) && (req_addr < 32'h2000);

    // ---------------- memory ----------------
    logic [31:0] ram_rdata;

    sram_mem #(.WORDS(MEM_WORDS)) u_mem (
        .clk(clk),
        .en   (req_en & sel_ram),
        .addr (req_addr[AW+1:2]),
        .wdata(req_wdata),
        .wstrb(req_wstrb),
        .rdata(ram_rdata)
    );

    // ---------------- memory-mapped UART ----------------
    logic       tx_start, tx_busy;
    logic [7:0] tx_data;

    assign tx_start = req_en & sel_uart & (req_wstrb != 4'b0000)
                              & (req_addr[11:0] == 12'h000);
    assign tx_data  = req_wdata[7:0];

    uart_tx #(.CLK_FREQ(CLK_FREQ), .BAUD_RATE(BAUD_RATE)) u_tx (
        .clk(clk), .rst(rst),
        .tx_start(tx_start), .tx_data(tx_data),
        .tx(uart_tx_pin), .tx_busy(tx_busy)
    );

    // ---------------- read data mux ----------------
    // Registered to match the memory's one-cycle read latency, so every read on
    // this bus has the same timing regardless of target.
    logic sel_uart_q;
    always_ff @(posedge clk) sel_uart_q <= req_en & sel_uart;

    assign mem_rdata = sel_uart_q ? {31'd0, tx_busy} : ram_rdata;
endmodule