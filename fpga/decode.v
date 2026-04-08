/* Decoder for TinyQV.

    Note parts of this are from the excellent FemtoRV by Bruno Levy et al.
*/

module tinyqv_decoder #(parameter REG_ADDR_BITS=4) (
    input [31:0] instr,

    output reg [31:0] imm,

    output reg is_load,
    output reg is_alu_imm,
    output reg is_auipc,
    output reg is_store,
    output reg is_alu_reg,
    output reg is_lui,
    output reg is_branch,
    output reg is_jalr,
    output reg is_jal,
    output reg is_ret,
    output reg is_system,

    output [2:1] instr_len,

    output reg [3:0] alu_op,  // See tinyqv_alu for format

    output reg [2:0] mem_op,      // Bit 0 of mem_op indicates branch condition is reversed

    output reg [REG_ADDR_BITS-1:0] rs1,
    output reg [REG_ADDR_BITS-1:0] rs2,
    output reg [REG_ADDR_BITS-1:0] rd
);

    wire [31:0] Uimm = {    instr[31],   instr[30:12], {12{1'b0}}};
    wire [31:0] Iimm = {{21{instr[31]}}, instr[30:20]};
    wire [31:0] Simm = {{21{instr[31]}}, instr[30:25],instr[11:7]};
    wire [31:0] Bimm = {{20{instr[31]}}, instr[7],instr[30:25],instr[11:8],1'b0};
    wire [31:0] Jimm = {{12{instr[31]}}, instr[19:12],instr[20],instr[30:21],1'b0};


    always @(*) begin
        is_ret = 0;
        is_load    = 0;
        is_alu_imm = 0;
        is_auipc   = 0;
        is_store   = 0;
        is_alu_reg = 0;
        is_lui     = 0;
        is_branch  = 0;
        is_jalr    = 0;
        is_jal     = 0;
        is_system  = 0;
        imm    = {32{1'bx}};
        alu_op = 4'b0000;
        mem_op = 3'bxxx;
        rs1 = {REG_ADDR_BITS{1'bx}};
        rs2 = {REG_ADDR_BITS{1'bx}};
        rd  = {REG_ADDR_BITS{1'bx}};

        if (instr[1:0] == 2'b11) begin
            // All Load insns (I-Type)
            is_load    =  (instr[6:2] == 5'b00000); // rd <- mem[rs1+Iimm]
            // All ALU immediate ops (I-Type)
            is_alu_imm =  (instr[6:2] == 5'b00100); // rd <- rs1 OP Iimm
            // Add Upper Immediate to PC (I-Type)
            is_auipc   =  (instr[6:2] == 5'b00101); // rd <- PC + Uimm
            // Store (S-Type)
            is_store   =  (instr[6:2] == 5'b01000); // mem[rs1+Simm] <- rs2
            // All ALU ops (R-Type)
            is_alu_reg =  (instr[6:2] == 5'b01100); // rd <- rs1 OP rs2
            // Load Upper Immediate (I-Type)
            is_lui     =  (instr[6:2] == 5'b01101); // rd <- Uimm
            // Compare and Jump (B-Type)
            is_branch  =  (instr[6:2] == 5'b11000); // if(rs1 OP rs2) PC<-PC+Bimm
            // Jump and Link Register (I-Type)
            is_jalr    =  (instr[6:2] == 5'b11001); // rd <- PC+4; PC<-rs1+Iimm
            // Jump and Link
            is_jal     =  (instr[6:2] == 5'b11011); // rd <- PC+4; PC<-PC+Jimm
            // System (ECALL/EBREAK/MRET/CSR)
            is_system  =  (instr[6:2] == 5'b11100);

            // Determine immediate.  Hopefully muxing here is reasonable.
            if (is_auipc || is_lui) imm = Uimm;
            else if (is_store) imm = Simm;
            else if (is_branch) imm = Bimm;
            else if (is_jal) imm = Jimm;
            else imm = Iimm;

            // Determine alu op
            if (is_load || is_auipc || is_store || is_jalr || is_jal) alu_op = 4'b0000;  // ADD
            else if (is_branch) alu_op = {1'b0, !instr[14], instr[14:13]};
            else alu_op = {instr[30] && (instr[5] || instr[13:12] == 2'b01),instr[14:12]};

            mem_op = instr[14:12];

            rs1 = instr[15+:REG_ADDR_BITS];
            rs2 = instr[20+:REG_ADDR_BITS];
            rd  = instr[ 7+:REG_ADDR_BITS];
        end
    end

    assign instr_len = (instr[1:0] == 2'b11) ? 2'b10 : 2'b01;

endmodule
