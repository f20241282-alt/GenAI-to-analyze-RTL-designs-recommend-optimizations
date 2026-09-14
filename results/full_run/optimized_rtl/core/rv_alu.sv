// ---------------------------------------------------------------------------
// rv_alu -- RV32I-style ALU
//
// OPTIMISATION TARGET (T2 balanced_mux_tree, T7 logic_restructure)
//   The result selection is a 16-deep priority chain over a fully-decoded
//   4-bit opcode, sitting *after* the shifter and the adder. That chain is on
//   the clk_core critical path.
// ---------------------------------------------------------------------------
`default_nettype none

module rv_alu (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        en,
    input  wire [3:0]  op,
    input  wire [31:0] rs1,
    input  wire [31:0] rs2,
    output reg  [31:0] rd_q,
    output reg         zero_q
);

  localparam [3:0] OP_ADD  = 4'd0,  OP_SUB  = 4'd1,  OP_SLL  = 4'd2,
                   OP_SLT  = 4'd3,  OP_SLTU = 4'd4,  OP_XOR  = 4'd5,
                   OP_SRL  = 4'd6,  OP_SRA  = 4'd7,  OP_OR   = 4'd8,
                   OP_AND  = 4'd9,  OP_MINU = 4'd10, OP_MAXU = 4'd11,
                   OP_ROL  = 4'd12, OP_ROR  = 4'd13, OP_ANDN = 4'd14,
                   OP_ORN  = 4'd15;

  wire [4:0]  sh    = rs2[4:0];
  wire [31:0] add_r = rs1 + rs2;
  wire [31:0] sub_r = rs1 - rs2;
  wire        ltu   = (rs1 < rs2);
  wire        lts   = ($signed(rs1) < $signed(rs2));
  wire [31:0] sll_r = rs1 << sh;
  wire [31:0] srl_r = rs1 >> sh;
  wire [31:0] sra_r = $signed(rs1) >>> sh;
  wire [31:0] rol_r = (rs1 << sh) | (rs1 >> ((32 - sh) & 5'h1f));
  wire [31:0] ror_r = (rs1 >> sh) | (rs1 << ((32 - sh) & 5'h1f));

  wire [31:0] alu_c;

  // genrtl-target: ternary_chain sel=op width=16
  assign alu_c = (op[3] ? (op[2] ? (op[1] ? (op[0] ? ((rs1 | ~rs2)) : ((rs1 & ~rs2))) : (op[0] ? (ror_r) : (rol_r))) : (op[1] ? (op[0] ? ((ltu ? rs2 : rs1)) : ((ltu ? rs1 : rs2))) : (op[0] ? ((rs1 & rs2)) : ((rs1 | rs2))))) : (op[2] ? (op[1] ? (op[0] ? (sra_r) : (srl_r)) : (op[0] ? ((rs1 ^ rs2)) : ({31'd0, ltu}))) : (op[1] ? (op[0] ? ({31'd0, lts}) : (sll_r)) : (op[0] ? (sub_r) : (add_r)))));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_q   <= 32'd0;
      zero_q <= 1'b0;
    end else if (en) begin
      rd_q   <= alu_c;
      zero_q <= (alu_c == 32'd0);
    end
  end

endmodule

`default_nettype wire
