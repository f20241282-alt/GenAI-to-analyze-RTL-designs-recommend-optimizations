// ---------------------------------------------------------------------------
// rv_lane -- register file + operand pipeline + ALU
//
// The operand registers exist so that the ALU sees a clean register-to-register
// path. Without them the worst path would start at a primary input, run through
// the 32:1 register-file read mux and then the whole ALU, which is not a
// realistic single-cycle structure and would swamp every other path.
// ---------------------------------------------------------------------------
`default_nettype none

module rv_lane #(
    parameter integer LANE_ID = 0
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  alu_op,
    input  wire [4:0]  rs1_addr,
    input  wire [4:0]  rs2_addr,
    input  wire [4:0]  rd_addr,
    input  wire        rd_we,
    input  wire [31:0] din,
    output wire [31:0] result,
    output wire        zero
);

  wire [31:0] rs1_d, rs2_d, alu_d;

  rv_regfile u_rf (
      .clk(clk), .rst_n(rst_n),
      .we(rd_we), .waddr(rd_addr), .wdata(alu_d),
      .raddr0(rs1_addr ^ LANE_ID[4:0]),
      .raddr1(rs2_addr),
      .rdata0(rs1_d), .rdata1(rs2_d));

  reg [31:0] rs1_q, rs2_q;
  reg [3:0]  op_q;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rs1_q <= 32'd0;
      rs2_q <= 32'd0;
      op_q  <= 4'd0;
    end else begin
      rs1_q <= rs1_d ^ din;
      rs2_q <= rs2_d;
      op_q  <= alu_op ^ LANE_ID[3:0];
    end
  end

  rv_alu u_alu (
      .clk(clk), .rst_n(rst_n), .en(1'b1),
      .op(op_q), .rs1(rs1_q), .rs2(rs2_q),
      .rd_q(alu_d), .zero_q(zero));

  assign result = alu_d;

endmodule

`default_nettype wire
