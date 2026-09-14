// ---------------------------------------------------------------------------
// dsp_mux_chain -- 16:1 selection written as a nested priority chain
//
// OPTIMISATION TARGET (T2 balanced_mux_tree)
//   sel is a 4-bit binary index and every one of the 16 values is covered, so
//   the priority chain has no priority semantics to preserve. Depth is 16
//   2:1 muxes; the equivalent balanced tree is depth 4. Complete SAT proof.
// ---------------------------------------------------------------------------
`default_nettype none

module dsp_mux_chain #(
    parameter integer W = 32
) (
    input  wire            clk,
    input  wire            rst_n,
    input  wire            en,
    input  wire [3:0]      sel,
    input  wire [16*W-1:0] din,
    output reg  [W-1:0]    dout_q
);

  wire [W-1:0] d [0:15];
  genvar gi;
  generate
    for (gi = 0; gi < 16; gi = gi + 1) begin : g_slice
      assign d[gi] = din[gi*W +: W];
    end
  endgenerate

  wire [W-1:0] mux_c;

  // genrtl-target: ternary_chain sel=sel width=16
  assign mux_c = (sel[3] ? (sel[2] ? (sel[1] ? (sel[0] ? (d[15]) : (d[14])) : (sel[0] ? (d[13]) : (d[12]))) : (sel[1] ? (sel[0] ? (d[11]) : (d[10])) : (sel[0] ? (d[9]) : (d[8])))) : (sel[2] ? (sel[1] ? (sel[0] ? (d[7]) : (d[6])) : (sel[0] ? (d[5]) : (d[4]))) : (sel[1] ? (sel[0] ? (d[3]) : (d[2])) : (sel[0] ? (d[1]) : (d[0])))));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)  dout_q <= {W{1'b0}};
    else if (en) dout_q <= mux_c;
  end

endmodule

`default_nettype wire
