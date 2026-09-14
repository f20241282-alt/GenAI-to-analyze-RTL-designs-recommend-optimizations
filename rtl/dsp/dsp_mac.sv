// ---------------------------------------------------------------------------
// dsp_mac -- single-cycle 16x16 multiply-accumulate
//
// OPTIMISATION TARGET (T3 pipeline_insert)
//   multiply -> add -> saturate sits in one combinational cloud between
//   input registers and acc_q. Splitting it into two stages cuts the path
//   roughly in half at the cost of +1 cycle of latency.
//
// genrtl-latency-flex: 2
//   The downstream consumer is valid-qualified (see dsp_lane), so up to two
//   extra pipeline stages are legal provided out_valid is delayed identically.
//   This contract is what licenses the latency-changing transform; the formal
//   gate proves it with a latency-offset sequential miter.
// ---------------------------------------------------------------------------
`default_nettype none

module dsp_mac #(
    parameter integer W   = 16,
    parameter integer ACC = 32
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              in_valid,
    input  wire [W-1:0]      a,
    input  wire [W-1:0]      b,
    input  wire [ACC-1:0]    c,
    output reg               out_valid,
    output reg  [ACC-1:0]    acc_q
);

  wire [2*W-1:0] prod_c = a * b;
  wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c} + c;
  wire [ACC-1:0] sat_c  = sum_c[ACC-1] ? {ACC{1'b1}} : sum_c;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc_q     <= {ACC{1'b0}};
      out_valid <= 1'b0;
    end else begin
      out_valid <= in_valid;
      if (in_valid) acc_q <= sat_c;
    end
  end

endmodule

`default_nettype wire
