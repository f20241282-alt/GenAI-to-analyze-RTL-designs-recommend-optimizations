// ---------------------------------------------------------------------------
// opcode_alu -- a small ALU whose result is selected by a long priority chain.
//
// This file is NOT part of the Nebula benchmark; it is a target for the
// standalone advisor. Opportunities the advisor is expected to flag:
//
//   * an 8-way priority ternary chain on `op` where every code is covered, so
//     it is really a balanced 3-level mux tree      (balanced_mux_tree)
//   * `a * 4` and `a / 2`, both powers of two        (strength reduction)
//   * an OR-of-equalities comparator chain on `op`   (comparator chain)
//   * a BLOCKING assignment inside a posedge block    (lint: use <=)
//
// The blocking-assignment line is an intentional style defect so the advisor's
// correctness checks have something to report on arbitrary RTL.
// ---------------------------------------------------------------------------
`default_nettype none

module opcode_alu (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [2:0]  op,
    input  wire [31:0] a,
    input  wire [31:0] b,
    output reg  [31:0] y_q,
    output reg         is_arith_q
);

  // 8-way priority chain over a 3-bit select that covers every code.
  wire [31:0] y_c = (op == 3'd0) ? (a + b)      :
                    (op == 3'd1) ? (a - b)      :
                    (op == 3'd2) ? (a & b)      :
                    (op == 3'd3) ? (a | b)      :
                    (op == 3'd4) ? (a ^ b)      :
                    (op == 3'd5) ? (a * 4)      :   // strength reduction: a << 2
                    (op == 3'd6) ? (a / 2)      :   // strength reduction: a >> 1
                                   (b);

  // OR-of-equalities: op in {0,1,5,6} -> arithmetic. Really a range/decode.
  wire is_arith_c = (op == 3'd0) | (op == 3'd1) | (op == 3'd5) | (op == 3'd6);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      y_q        <= 32'd0;
      is_arith_q <= 1'b0;
    end else begin
      y_q        <= y_c;
      is_arith_q = is_arith_c;   // BUG: blocking '=' in a sequential block
    end
  end

endmodule

`default_nettype wire
