// ---------------------------------------------------------------------------
// cdc_pulse_sync -- toggle-based single-pulse crossing between async domains
//
// genrtl-protect: cdc_pulse
//   PROTECTED. The toggle/edge-detect pair is the crossing; restructuring it
//   changes pulse semantics even when the logic is Boolean-equivalent.
// ---------------------------------------------------------------------------
`default_nettype none

(* genrtl_protect = "cdc_pulse" *)
(* keep_hierarchy *)
module cdc_pulse_sync (
    input  wire  src_clk,
    input  wire  src_rst_n,
    input  wire  src_pulse,
    input  wire  dst_clk,
    input  wire  dst_rst_n,
    output wire  dst_pulse
);

  (* keep *) reg toggle_r;
  always @(posedge src_clk or negedge src_rst_n) begin
    if (!src_rst_n)     toggle_r <= 1'b0;
    else if (src_pulse) toggle_r <= ~toggle_r;
  end

  wire toggle_sync;
  cdc_sync_2ff #(.WIDTH(1), .STAGES(3)) u_sync (
      .dst_clk  (dst_clk),
      .dst_rst_n(dst_rst_n),
      .d_src    (toggle_r),
      .q_dst    (toggle_sync)
  );

  (* keep *) reg toggle_dly_r;
  always @(posedge dst_clk or negedge dst_rst_n) begin
    if (!dst_rst_n) toggle_dly_r <= 1'b0;
    else            toggle_dly_r <= toggle_sync;
  end

  assign dst_pulse = toggle_sync ^ toggle_dly_r;

endmodule

`default_nettype wire
