// ---------------------------------------------------------------------------
// cdc_sync_2ff -- classic two-flop synchroniser
//
// genrtl-protect: cdc_synchronizer
//   PROTECTED. Retiming, duplication or "optimisation" of a 2-FF synchroniser
//   destroys the MTBF property that makes the crossing legal. The optimiser is
//   hard-blocked from emitting patches inside this file.
// ---------------------------------------------------------------------------
`default_nettype none

(* genrtl_protect = "cdc_synchronizer" *)
(* keep_hierarchy *)
module cdc_sync_2ff #(
    parameter integer WIDTH  = 1,
    parameter integer STAGES = 2
) (
    input  wire              dst_clk,
    input  wire              dst_rst_n,
    input  wire [WIDTH-1:0]  d_src,
    output wire [WIDTH-1:0]  q_dst
);

  (* keep *) (* async_reg = "true" *) reg [WIDTH-1:0] sync_r [0:STAGES-1];

  integer s;
  always @(posedge dst_clk or negedge dst_rst_n) begin
    if (!dst_rst_n) begin
      for (s = 0; s < STAGES; s = s + 1) sync_r[s] <= {WIDTH{1'b0}};
    end else begin
      sync_r[0] <= d_src;
      for (s = 1; s < STAGES; s = s + 1) sync_r[s] <= sync_r[s-1];
    end
  end

  assign q_dst = sync_r[STAGES-1];

endmodule

`default_nettype wire
