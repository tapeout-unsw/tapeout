`timescale 1ns / 1ps

module decoder(
    input  logic [31:0] inst,
    output logic [6:0] opc,
    output logic [4:0] rd,
    output logic [4:0] rs1,
    output logic [4:0] rs2,
    output logic [2:0] func3,
    output logic [6:0] func7,
    output logic reg_write,
    output logic [2:0] imm_sel,
    output logic [3:0] alu_op,
    output logic alu_src,
    output logic branch,
    output logic jmp,
    output logic jmpr,
    output logic mem_read,
    output logic mem_write,
    output logic lui,
    output logic auipc

);

    //field extraction from instruction
    assign opc   = inst[6:0];
    assign rd    = inst[11:7];
    assign func3 = inst[14:12];
    assign func7 = inst[31:25];
    assign rs1   = inst[19:15];
    assign rs2   = inst[24:20];
    
    always_comb begin
        // defaults - every control signal gets a safe value
        reg_write = 1'b0;
        alu_src   = 1'b0;
        imm_sel   = 3'd0;       // I-format as a harmless default
        alu_op    = 4'd0;       // ADD placeholder for Pass 1
        branch    = 1'b0;
        jmp       = 1'b0;
        jmpr      = 1'b0;
        mem_read  = 1'b0;
        mem_write = 1'b0;
        lui       = 1'b0;
        auipc     = 1'b0;
        
        case (opc)
        7'b0110011: begin   // R-type, use func7 and func3 to decode instruction
            case (func3)
                 3'b000: alu_op = (func7[5]) ? 4'd1 : 4'd0;  // SUB or ADD depending on func7
                 3'b111: alu_op = 4'd2;  //AND
                 3'b110: alu_op = 4'd3; //or
                 3'b100: alu_op = 4'd4; //xor
                 3'b001: alu_op = 4'd5; //sll
                 3'b101: alu_op = (func7[5]) ? 4'd7 : 4'd6;  // srl or sra depending on func7
                 3'b010: alu_op = 4'd8; //slt
                 3'b011: alu_op = 4'd9; //sltu
                 
                 default: alu_op = 4'd0;
            endcase
            reg_write = 1'b1;
        end
        
        7'b0010011: begin // I-type
            reg_write = 1'b1;
            alu_src = 1'b1;
            imm_sel = 3'd0;
            case (func3)
                 3'b000: alu_op = 4'd0;  // always add
                 3'b111: alu_op = 4'd2;  //AND
                 3'b110: alu_op = 4'd3; //or
                 3'b100: alu_op = 4'd4; //xor
                 3'b001: alu_op = 4'd5; //sll
                 3'b101: alu_op = (func7[5]) ? 4'd7 : 4'd6;  // srai or srli depending on func7
                 3'b010: alu_op = 4'd8; //slt
                 3'b011: alu_op = 4'd9; //sltu
                 
                 default: alu_op = 4'd0;
            endcase
        end
        
        7'b0000011: begin //load memory -> reg
            reg_write = 1'b1;
            alu_src   = 1'b1;
            imm_sel   = 3'd0;
            mem_read  = 1'b1;
            
        end
        
        7'b0100011: begin //store reg->mem
            alu_src   = 1'b1;
            imm_sel   = 3'd1;
            mem_write = 1'b1;
        end
        
        7'b1100011: begin //branch
            imm_sel = 3'd2;
            branch  = 1'd1;
        end
        
        7'b0110111: begin  // Load upper immediate
            reg_write = 1'b1;
            imm_sel   = 3'd3;   // U-format
            lui       = 1'b1;
        end
        
        7'b1101111: begin //jump and link
            reg_write = 1'b1;
            imm_sel   = 3'd4;
            jmp       = 1'd1;
            
        end
        
        7'b1100111: begin //jump and link register
            reg_write = 1'b1;
            imm_sel   = 3'd0;
            jmpr      = 1'd1;           
        end
        
        7'b0010111: begin  // AUIPC
            reg_write = 1'b1;
            imm_sel   = 3'd3;   // U-format
            auipc     = 1'b1;
        end
        
        default: ;
    endcase
    end
    
endmodule
