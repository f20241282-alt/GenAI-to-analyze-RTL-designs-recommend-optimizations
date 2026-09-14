// ---------------------------------------------------------------------------
// fir4_unpipelined -- 4-tap FIR filter, deliberately un-optimised.
//
// This file is NOT part of the Nebula benchmark. It exists to demonstrate the
// standalone advisor (`python -m genrtl suggest examples/fir4_unpipelined.sv`)
// on arbitrary RTL. It elaborates cleanly and contains several independent
// optimisation opportunities the advisor is expected to find:
//
//   * a multiply -> add -> add -> add combinational cloud between the input
//     registers and acc_q  (pipeline_insert / balanced_adder_tree)
//   * a constant multiply by 2 that is really a shift  (strength reduction)
//   * a left-associative 4-term addition chain          (balanced adder tree)
//
// genrtl-latency-flex: 2
//   The consumer is valid-qualified, so up to two pipeline stages are legal
//   provided out_valid is delayed identically. This contract is what lets the
//   verified engine treat pipeline insertion here as auto-appliable.
// ---------------------------------------------------------------------------
`default_nettype none

module fir4_unpipelined #(
    parameter integer W = 12
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire                 in_valid,
    input  wire signed [W-1:0]  x0,
    input  wire signed [W-1:0]  x1,
    input  wire signed [W-1:0]  x2,
    input  wire signed [W-1:0]  x3,
    output reg                  out_valid,
    output reg  signed [2*W+1:0] acc_q
);

  // Coefficients {2, 3, 3, 2}. The *2 taps are shifts, not multipliers.
  wire signed [2*W-1:0] m0 = x0 * 2;      // strength reduction: x0 << 1
  wire signed [2*W-1:0] m1 = x1 * 3;
  wire signed [2*W-1:0] m2 = x2 * 3;
  wire signed [2*W-1:0] m3 = x3 * 2;      // strength reduction: x3 << 1

  // Left-associative 4-term add chain: depth 3, rebalances to depth 2.
  wire signed [2*W+1:0] sum_c = m0 + m1 + m2 + m3;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc_q     <= '0;
      out_valid <= 1'b0;
    end else begin
      out_valid <= in_valid;
      if (in_valid) acc_q <= sum_c;
    end
  end

endmodule

`default_nettype wire
