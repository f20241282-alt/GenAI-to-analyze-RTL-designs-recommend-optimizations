// ---------------------------------------------------------------------------
// dsp_adder_chain -- 8-term accumulate, written as a left-associative chain
//
// OPTIMISATION TARGET (T1 balanced_adder_tree)
//   The reduction below is a linear chain: a0+a1 -> +a2 -> +a3 ... -> +a7.
//   Logic depth is 7 ripple adders, which is the dominant setup path of the
//   clk_dsp domain. Integer addition is associative modulo 2**W, so the chain
//   may be rebalanced into a depth-3 tree with *identical* combinational
//   function -- provable by a complete SAT equivalence check.
//
// Not protected: pure datapath, no CDC content.
// ---------------------------------------------------------------------------
`default_nettype none

module dsp_adder_chain #(
    parameter integer W = 16
) (
    input  wire            clk,
    input  wire            rst_n,
    input  wire            en,
    input  wire [W-1:0]    a0,
    input  wire [W-1:0]    a1,
    input  wire [W-1:0]    a2,
    input  wire [W-1:0]    a3,
    input  wire [W-1:0]    a4,
    input  wire [W-1:0]    a5,
    input  wire [W-1:0]    a6,
    input  wire [W-1:0]    a7,
    output reg  [W-1:0]    sum_q
);

  wire [W-1:0] sum_c;

  // genrtl-target: reduction_chain op=+ terms=8
  assign sum_c = a0 + a1 + a2 + a3 + a4 + a5 + a6 + a7;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)     sum_q <= {W{1'b0}};
    else if (en)    sum_q <= sum_c;
  end

endmodule

`default_nettype wire
