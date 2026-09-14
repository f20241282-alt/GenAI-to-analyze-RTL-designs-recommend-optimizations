// ---------------------------------------------------------------------------
// dsp_lane -- one DSP slice: 16:1 select -> 8-term reduce -> MAC
//
// Each sub-block terminates in its own register, so the lane exposes three
// independent critical paths for the optimiser to attack:
//   (a) reg -> 16-deep priority mux -> reg     (dsp_mux_chain)
//   (b) reg -> 7-deep adder chain    -> reg    (dsp_adder_chain)
//   (c) reg -> multiply + accumulate -> reg    (dsp_mac)
//
// The lane output is valid-qualified, which is what makes latency-changing
// transforms legal inside (c).
// ---------------------------------------------------------------------------
`default_nettype none

module dsp_lane #(
    parameter integer LANE_ID = 0
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         en,
    input  wire [3:0]   sel,
    input  wire [31:0]  seed,
    input  wire         in_valid,
    output wire         out_valid,
    output wire [31:0]  result
);

  // ---- operand expansion (cheap pseudo-random spread, keeps cells busy) ----
  wire [16*32-1:0] operands;
  genvar gi;
  generate
    for (gi = 0; gi < 16; gi = gi + 1) begin : g_op
      assign operands[gi*32 +: 32] = seed ^ (32'h9E37_79B9 * (gi + LANE_ID + 1));
    end
  endgenerate

  // ---- (a) selection ------------------------------------------------------
  wire [31:0] picked;
  dsp_mux_chain #(.W(32)) u_mux (
      .clk(clk), .rst_n(rst_n), .en(en), .sel(sel),
      .din(operands), .dout_q(picked));

  // ---- (b) reduction ------------------------------------------------------
  wire [15:0] sum16;
  dsp_adder_chain #(.W(16)) u_add (
      .clk(clk), .rst_n(rst_n), .en(en),
      .a0(picked[15:0]),
      .a1(picked[31:16]),
      .a2(seed[15:0]),
      .a3(seed[31:16]),
      .a4(picked[15:0]  ^ seed[15:0]),
      .a5(picked[31:16] ^ seed[31:16]),
      .a6({picked[7:0],  seed[7:0]}),
      .a7({seed[23:16],  picked[23:16]}),
      .sum_q(sum16));

  // ---- (c) MAC ------------------------------------------------------------
  dsp_mac #(.W(16), .ACC(32)) u_mac (
      .clk(clk), .rst_n(rst_n),
      .in_valid(in_valid),
      .a(sum16),
      .b(picked[15:0]),
      .c({16'd0, sum16}),
      .out_valid(out_valid),
      .acc_q(result));

endmodule

`default_nettype wire
