module amoswap (
    input  logic        clk,
    input  logic        rst,

    input  logic        start,
    input  logic [12:0] address,
    input  logic [31:0] replacement,

    output logic        busy,
    output logic        done,
    output logic [31:0] old_value,

    output logic        mem_req,
    output logic [12:0] mem_addr,
    output logic [31:0] mem_wdata,
    output logic [3:0]  mem_wstrb,

    input  logic        mem_gnt,
    input  logic [31:0] mem_rdata
);

    typedef enum logic [1:0] {
        IDLE,
        READ,
        RESPONSE,
        WRITE
    } state_t;

    state_t state;

    logic [12:0] address_q;
    logic [31:0] replacement_q;

    assign busy      = (state != IDLE);
    assign mem_req   = (state == READ) || (state == WRITE);
    assign mem_addr  = address_q;
    assign mem_wdata = replacement_q;
    assign mem_wstrb = (state == WRITE) ? 4'b1111 : 4'b0000;
    assign done      = (state == WRITE) && mem_gnt;

    always_ff @(posedge clk) begin
        if (rst) begin
            state         <= IDLE;
            address_q     <= 13'd0;
            replacement_q <= 32'd0;
            old_value     <= 32'd0;
        end else begin
            case (state)
                IDLE: begin
                    if (start) begin
                        address_q     <= address;
                        replacement_q <= replacement;
                        state         <= READ;
                    end
                end

                READ: begin
                    if (mem_gnt)
                        state <= RESPONSE;
                end

                RESPONSE: begin
                    old_value <= mem_rdata;
                    state     <= WRITE;
                end

                WRITE: begin
                    if (mem_gnt)
                        state <= IDLE;
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule