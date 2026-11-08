`timescale 1ns / 1ps
//
// riskyC1-MC : multicycle RV32E core.
//
// Parameters
//   NREGS   16 = RV32E (default), 32 = RV32I.
//
// Instruction port
//   i_req    high in S_FETCH. i_addr = pc[12:0], stable while i_req is high.
//   i_valid  the instruction is on i_rdata this cycle; latched into IR.
//
// Data port, byte-addressed, 13-bit address
//   Ordinary accesses are registered at the end of S_EXEC.
//   d_gnt accepts an access; load data returns the following cycle.
//   d_atomic is high while u_amoswap performs its read/write sequence.
//   Misaligned accesses are not supported.
//
// FSM
//   S_FETCH    present PC, wait for i_valid, latch IR
//   S_EXEC     decode, register read, ALU; start memory access or AMOSWAP
//   S_MEM      present the registered data access, wait for d_gnt
//   S_MEM_W    register load data in ld_q
//   S_AMO_WAIT wait for u_amoswap; write the old word to rd when done
//
// Ordinary loads write back in the first S_FETCH cycle after S_MEM_W,
// while IR still holds the load.
//
module core #(
    parameter int NREGS = 16
) (
    input  logic        clk,
    input  logic        rst,

    // Instruction port
    output logic        i_req,
    output logic [12:0] i_addr,
    input  logic        i_valid,
    input  logic [31:0] i_rdata,

    // Data port
    output logic        d_req,
    output logic [12:0] d_addr,       // BYTE address, word aligned
    output logic [31:0] d_wdata,
    output logic [3:0]  d_wstrb,      // 0 = read
    input  logic        d_gnt,
    input  logic [31:0] d_rdata,
    output logic        d_atomic,

    // Observability: pulses high for one cycle as each instruction completes.
    output logic        retire,
    output logic [31:0] retire_pc
);
    typedef enum logic [2:0] {
        S_FETCH, S_EXEC, S_MEM, S_MEM_W, S_AMO_WAIT
    } state_e;

    state_e      state;
    logic [31:0] pc, ir;

    // ---------------- decode ----------------
    logic [4:0] rd, rs1, rs2;
    logic [2:0] func3, imm_sel;
    logic [3:0] alu_op;
    logic       reg_write, alu_src, branch, jmp, jmpr, mem_read, mem_write, lui, auipc;

    logic        amo_swap, amo_busy, amo_done, amo_req;
    logic [12:0] amo_addr;
    logic [31:0] amo_wdata, amo_old;
    logic [3:0]  amo_wstrb;

    decoder u_decoder (
        .inst(ir), .opc(), .rd(rd), .rs1(rs1), .rs2(rs2),
        .func3(func3), .func7(),
        .reg_write(reg_write), .imm_sel(imm_sel), .alu_op(alu_op),
        .alu_src(alu_src), .branch(branch), .jmp(jmp), .jmpr(jmpr),
        .mem_read(mem_read), .mem_write(mem_write), .lui(lui), .auipc(auipc),
        .amo_swap(amo_swap)
    );

    logic [31:0] imm;
    imm_gen u_imm_gen (.inst(ir), .ctrl(imm_sel), .imm(imm));

    // ---------------- register file ----------------
    logic [31:0] rs1_data, rs2_data, wb_data;
    logic        rf_we;

    register_file #(.NREGS(NREGS), .BYPASS(1'b0)) u_rf (
        .clk(clk),
        .rs1_addr(rs1), .rs1_data(rs1_data),
        .rs2_addr(rs2), .rs2_data(rs2_data),
        .rd_we(rf_we), .rd_addr(rd), .rd_data(wb_data)
    );

    // ---------------- ALU ----------------
    logic [31:0] alu_b, alu_result;
    assign alu_b = alu_src ? imm : rs2_data;
    alu u_alu (.a(rs1_data), .b(alu_b), .ctrl(alu_op), .res(alu_result));

    // ---------------- branch comparison ----------------
    // Separate from the ALU: for branches the decoder leaves alu_op at ADD so
    // the ALU is available for address arithmetic.
    logic eq, lt, ltu, branch_taken;
    assign eq  = (rs1_data == rs2_data);
    assign lt  = ($signed(rs1_data) < $signed(rs2_data));
    assign ltu = (rs1_data < rs2_data);

    always_comb begin
        case (func3)
            3'b000:  branch_taken = branch &  eq;    // beq
            3'b001:  branch_taken = branch & ~eq;    // bne
            3'b100:  branch_taken = branch &  lt;    // blt
            3'b101:  branch_taken = branch & ~lt;    // bge
            3'b110:  branch_taken = branch &  ltu;   // bltu
            3'b111:  branch_taken = branch & ~ltu;   // bgeu
            default: branch_taken = 1'b0;
        endcase
    end

    // ---------------- byte / halfword handling ----------------
    logic [31:0] st_wdata, load_data, ld_q;
    logic        ld_wb;      // ld_q holds a load to write back this cycle
    logic [3:0]  st_wstrb;

    mem_access u_mem_access (
        .func3(func3), .addr_lo(alu_result[1:0]), .mem_write(mem_write),
        .store_data(rs2_data), .w_data(st_wdata), .w_strb(st_wstrb),
        .raw_rdata(ld_q), .load_data(load_data)
    );

    // ---------------- next PC ----------------
    logic [31:0] next_pc;
    always_comb begin
        if      (branch_taken) next_pc = pc + imm;
        else if (jmp)          next_pc = pc + imm;
        else if (jmpr)         next_pc = (rs1_data + imm) & 32'hFFFF_FFFE;
        else                   next_pc = pc + 32'd4;
    end

    // ---------------- writeback mux ----------------
    always_comb begin
        if      (amo_swap)   wb_data = amo_old;
        else if (lui)        wb_data = imm;
        else if (auipc)      wb_data = pc + imm;
        else if (jmp | jmpr) wb_data = pc + 32'd4;
        else if (mem_read)   wb_data = load_data;
        else                 wb_data = alu_result;
    end

    // ---------------- memory ports ----------------
    assign i_req   = (state == S_FETCH);
    assign i_addr  = pc[12:0];

    // The access is computed in S_EXEC and registered, so S_MEM starts from
    // flops rather than the register file and ALU. Its inputs do not change
    // between S_EXEC and S_MEM, so the values are the same.
    logic [12:0] d_addr_q;
    logic [31:0] d_wdata_q;
    logic [3:0]  d_wstrb_q;
    always_ff @(posedge clk) begin
        if (rst) begin
            d_addr_q  <= '0;
            d_wdata_q <= '0;
            d_wstrb_q <= '0;
        end else if (state == S_EXEC) begin
            d_addr_q  <= {alu_result[12:2], 2'b00};
            d_wdata_q <= st_wdata;
            d_wstrb_q <= mem_write ? st_wstrb : 4'b0000;
        end
    end

    // ---------------- amoswap ----------------
    amoswap u_amoswap (
        .clk(clk), .rst(rst), .start(state == S_EXEC && amo_swap),
        .address({rs1_data[12:2], 2'b00}), .replacement(rs2_data),
        .busy(amo_busy), .done(amo_done), .old_value(amo_old),
        .mem_req(amo_req), .mem_addr(amo_addr), .mem_wdata(amo_wdata),
        .mem_wstrb(amo_wstrb), .mem_gnt(d_gnt && state == S_AMO_WAIT),
        .mem_rdata(d_rdata)
    );

    assign d_atomic = (state == S_AMO_WAIT) && amo_busy;

    assign d_req   = (state == S_AMO_WAIT) ? amo_req   : (state == S_MEM);
    assign d_addr  = (state == S_AMO_WAIT) ? amo_addr  : d_addr_q;
    assign d_wdata = (state == S_AMO_WAIT) ? amo_wdata : d_wdata_q;
    assign d_wstrb = (state == S_AMO_WAIT) ? amo_wstrb : d_wstrb_q;

    // Register write: EXEC for ALU-class instructions, the cycle after MEM_W
    // for ordinary loads, and completion for AMOSWAP.
    assign rf_we = reg_write & ( (state == S_EXEC && !mem_read && !mem_write && !amo_swap)
                               | ld_wb
                               | (state == S_AMO_WAIT && amo_done) );

    always_ff @(posedge clk) begin
        if (rst) begin
            ld_wb <= 1'b0;
            ld_q  <= 32'd0;
        end else begin
            ld_wb <= (state == S_MEM_W);
            if (state == S_MEM_W) ld_q <= d_rdata;
        end
    end

    assign retire    = (state == S_EXEC && !mem_read && !mem_write && !amo_swap)
                     | (state == S_MEM  &&  mem_write && d_gnt)
                     | (state == S_MEM_W)
                     | (state == S_AMO_WAIT && amo_done);
    assign retire_pc = pc;

    // ---------------- FSM ----------------
    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_FETCH;
            pc    <= 32'd0;
            ir    <= 32'd0;
        end else begin
            case (state)
                S_FETCH: if (i_valid) begin ir <= i_rdata; state <= S_EXEC; end
                S_EXEC: begin
                    if (amo_swap)                  state <= S_AMO_WAIT;
                    else if (mem_read | mem_write) state <= S_MEM;
                    else begin pc <= next_pc; state <= S_FETCH; end
                end
                S_MEM: if (d_gnt) begin
                    if (mem_read) state <= S_MEM_W;
                    else begin pc <= next_pc; state <= S_FETCH; end
                end
                S_MEM_W: begin pc <= next_pc; state <= S_FETCH; end
                S_AMO_WAIT: if (amo_done) begin pc <= next_pc; state <= S_FETCH; end
                default:   state <= S_FETCH;
            endcase
        end
    end
endmodule