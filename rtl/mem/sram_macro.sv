`timescale 1ns / 1ps
//
// One 8-bit SRAM lane. The only file that knows the vendor cell names, pin
// names and active-low polarities; everything above it is active high.
//
// Parameters
//   WORDS   words in this lane. Macro builds support 256, 512 and 1024
//           (gf180mcu_ocd_ip_sram__sram<WORDS>x8m8wm1). The behavioural model
//           accepts any power of two.
//
// Behaviour, sampled on the rising edge of clk
//   en = 1, we = 0   read:  rdata = mem[addr] after the edge
//   en = 1, we = 1   write: mem[addr] = wdata at the edge; rdata unchanged
//   en = 0           idle:  memory and rdata unchanged
//
// Build modes
//   default                  behavioural model, same cycle timing as the macro
//   +define+USE_SRAM_MACRO   the OCD 3.3 V macro. Simulation also needs the
//                            models in ip/gf180mcu_ocd_ip_sram/, synthesis the
//                            __blackbox.v files. The macro's VDD/VSS pins only
//                            exist under USE_POWER_PINS and are connected by
//                            the physical-design flow, not here.
//
module sram_macro #(
    parameter int WORDS = 512,
    parameter int AW    = $clog2(WORDS)
) (
    input  logic          clk,
    input  logic          en,                  // access this cycle
    input  logic          we,                  // 1 = write, 0 = read
    input  logic [AW-1:0] addr,                // row
    input  logic [7:0]    wdata,               // byte to write
    output logic [7:0]    rdata                // valid the cycle after a read
);

`ifdef USE_SRAM_MACRO
    // Every macro control is active low.
    logic cen_n, gwen_n;
    assign cen_n  = ~en;                       // chip enable
    assign gwen_n = ~we;                       // global write enable: 0 = write

    // Three separate if blocks rather than an else-if chain, so every tool
    // names the instance <lane>.g_<WORDS>.u_sram.
    generate
        if (WORDS == 256) begin : g_256
            gf180mcu_ocd_ip_sram__sram256x8m8wm1 u_sram (
                .CLK (clk),
                .CEN (cen_n),
                .GWEN(gwen_n),
                .WEN (8'h00),                  // per-bit mask: write all 8 bits
                .A   (addr),
                .D   (wdata),
                .Q   (rdata)
            );
        end

        if (WORDS == 512) begin : g_512
            gf180mcu_ocd_ip_sram__sram512x8m8wm1 u_sram (
                .CLK (clk),
                .CEN (cen_n),
                .GWEN(gwen_n),
                .WEN (8'h00),
                .A   (addr),
                .D   (wdata),
                .Q   (rdata)
            );
        end

        if (WORDS == 1024) begin : g_1024
            gf180mcu_ocd_ip_sram__sram1024x8m8wm1 u_sram (
                .CLK (clk),
                .CEN (cen_n),
                .GWEN(gwen_n),
                .WEN (8'h00),
                .A   (addr),
                .D   (wdata),
                .Q   (rdata)
            );
        end
        // Any other WORDS leaves rdata undriven, which lint reports.
    endgenerate
`else
    logic [7:0] mem [0:WORDS-1];               // no initial block: starts undefined

    always_ff @(posedge clk) begin
        if (en) begin
            if (we) mem[addr] <= wdata;        // a write leaves rdata unchanged,
            else    rdata     <= mem[addr];    // exactly like the macro
        end
    end
`endif

endmodule