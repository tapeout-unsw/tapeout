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
//   6       0x1018   UART_RX      pop oldest RX byte         ignored
//   7       0x101C   CONTROL      bits 2:0 below             bits 3:0 below
// UART_STATUS: bit 0 TX busy, bit 1 RX ready, bit 2 RX overflow (sticky).
// CONTROL, writable by either core:
//   bit 0  RX_CLAIM  software owns RX; the bootloader ignores incoming bytes
//   bit 1  HOLD0     core 0 held in reset with its caches flushed
//   bit 2  HOLD1     core 1 held in reset with its caches flushed
//   bit 3  OVF_CLR   write 1 to clear RX overflow; reads 0
// A core's write to its own HOLD bit is ignored, so a core never stops itself
// and the two cores can never both be held.
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
    input  logic [7:0]  rx_data,
    input  logic        rx_valid,
    output logic        rx_claim,
    output logic [1:0]  core_hold,

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

    // Sixteen bytes absorb bus latency while software services the UART.
    logic [7:0] rx_fifo [0:15];
    logic [3:0] rx_head, rx_tail;
    logic [4:0] rx_count;
    logic rx_overflow, rx_push, rx_pop;
    assign rx_pop = rd && m_addr == 3'd6 && rx_count != 0;
    assign rx_push = rx_valid && rx_claim && (rx_count < 16 || rx_pop);

    always_ff @(posedge clk) begin
        if (rst || !core_run) begin
            rx_claim <= 0;
            core_hold <= 0;
            rx_head <= 0;
            rx_tail <= 0;
            rx_count <= 0;
            rx_overflow <= 0;
        end else begin
            if (wr && m_addr == 3'd7) begin
                rx_claim <= m_wdata[0];
                // Each core may hold only the other one.
                if (m_core) core_hold[0] <= m_wdata[1];
                else        core_hold[1] <= m_wdata[2];
                if (m_wdata[3]) rx_overflow <= 0;
            end
            if (rx_valid && rx_claim && !rx_push) rx_overflow <= 1;
            if (rx_push) begin
                rx_fifo[rx_tail] <= rx_data;
                rx_tail <= rx_tail + 1'b1;
            end
            if (rx_pop) rx_head <= rx_head + 1'b1;
            case ({rx_push, rx_pop})
                2'b10: rx_count <= rx_count + 1'b1;
                2'b01: rx_count <= rx_count - 1'b1;
                default: ;
            endcase
        end
    end

    // Registered reads return the value before this access's side effects.
    logic [7:0] rd_val;
    always_comb begin
        case (m_addr)
            3'd1:    rd_val = {5'd0, rx_overflow, (rx_count != 0), tx_busy};
            3'd2:    rd_val = {7'd0, lock};
            3'd3:    rd_val = {7'd0, m_core};
            3'd4:    rd_val = {6'd0, exit_q};
            3'd5:    rd_val = {7'd0, lock};
            3'd6:    rd_val = rx_count != 0 ? rx_fifo[rx_head] : 8'd0;
            3'd7:    rd_val = {5'd0, core_hold, rx_claim};
            default: rd_val = 0;
        endcase
    end

    logic [7:0] rd_q;
    always_ff @(posedge clk) begin
        if (rst) rd_q <= 0;
        else     rd_q <= rd ? rd_val : 8'd0;
    end
    assign m_rdata  = {24'd0, rd_q};

    assign tx_start = wr && (m_addr == 3'd0);
    assign tx_data  = m_wdata;

    assign done     = &exit_q;
    assign dbg_lock = lock;
    assign dbg_exit = exit_q;
endmodule
