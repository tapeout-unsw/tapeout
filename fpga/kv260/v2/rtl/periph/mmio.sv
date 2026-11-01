`timescale 1ns / 1ps
//
// Memory-mapped registers. Answers every access with one cycle of latency, like
// the SRAM, so the bus never waits.
//
//   m_addr  address  name         read                       write
//   0       0x1000   UART_DATA    0                          tx_start pulse, tx_data = m_wdata
//   1       0x1004   UART_STATUS  bit 0 = tx_busy            ignored
//   2       0x1008   LOCK         bit 0 = lock, then lock=1  lock = 0
//   3       0x100C   CORE_ID      bit 0 = m_core             ignored
//   4       0x1010   EXIT         bits 1:0 = exit            exit[m_core] = 1
//   5       0x1014   LOCK_PEEK    bit 0 = lock (optional)    ignored
//   6, 7             unused       0                          ignored
//
// m_rdata is registered: it carries the read result of the previous cycle's
// access. lock and exit clear while core_run is low. done = exit[0] & exit[1].
//
module mmio (
    input  logic        clk,
    input  logic        rst,
    input  logic        core_run,

    input  logic        m_en,
    input  logic [2:0]  m_addr,
    input  logic        m_we,
    input  logic [7:0]  m_wdata,
    input  logic        m_core,
    output logic [31:0] m_rdata,

    output logic        tx_start,
    output logic [7:0]  tx_data,
    input  logic        tx_busy,

    output logic        done,
    output logic        dbg_lock,
    output logic [1:0]  dbg_exit
);
    logic       wr, rd;
    assign wr = m_en &  m_we;
    assign rd = m_en & ~m_we;

    logic       lock;
    logic [1:0] exit_q;

    always_ff @(posedge clk) begin
        if (rst || !core_run) begin
            lock   <= 1'b0;
            exit_q <= 2'b00;
        end else begin
            if (rd && m_addr == 3'd2) lock <= 1'b1;     // LOCK read: test and set
            if (wr && m_addr == 3'd2) lock <= 1'b0;     // LOCK write: release
            if (wr && m_addr == 3'd4) exit_q[m_core] <= 1'b1;
        end
    end

    // Read value of the addressed register, before this cycle's update, so a
    // LOCK read returns the old value.
    logic [1:0] rd_val;
    always_comb begin
        case (m_addr)
            3'd1:    rd_val = {1'b0, tx_busy};
            3'd2:    rd_val = {1'b0, lock};
            3'd3:    rd_val = {1'b0, m_core};
            3'd4:    rd_val = exit_q;
            3'd5:    rd_val = {1'b0, lock};
            default: rd_val = 2'b00;
        endcase
    end

    // Only bits 1:0 of any register are ever non-zero.
    logic [1:0] rd_q;
    always_ff @(posedge clk) begin
        if (rst) rd_q <= 2'b00;
        else     rd_q <= rd ? rd_val : 2'b00;
    end
    assign m_rdata  = {30'd0, rd_q};

    assign tx_start = wr && (m_addr == 3'd0);
    assign tx_data  = m_wdata;

    assign done     = &exit_q;
    assign dbg_lock = lock;
    assign dbg_exit = exit_q;
endmodule
