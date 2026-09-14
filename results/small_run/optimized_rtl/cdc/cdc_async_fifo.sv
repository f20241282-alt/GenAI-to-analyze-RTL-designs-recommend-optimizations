// ---------------------------------------------------------------------------
// cdc_async_fifo -- dual-clock FIFO with Gray-coded pointer crossings
//
// genrtl-protect: cdc_async_fifo
//   PROTECTED. Gray pointers rely on exactly one bit toggling per increment.
//   Boolean-equivalent restructuring of the binary->Gray logic, or retiming of
//   the pointer registers, can break the single-bit-change invariant.
// ---------------------------------------------------------------------------
`default_nettype none

(* genrtl_protect = "cdc_async_fifo" *)
(* keep_hierarchy *)
module cdc_async_fifo #(
    parameter integer WIDTH = 32,
    parameter integer AW    = 4          // depth = 2**AW
) (
    input  wire              wclk,
    input  wire              wrst_n,
    input  wire              wpush,
    input  wire [WIDTH-1:0]  wdata,
    output wire              wfull,

    input  wire              rclk,
    input  wire              rrst_n,
    input  wire              rpop,
    output wire [WIDTH-1:0]  rdata,
    output wire              rempty
);

  localparam integer DEPTH = (1 << AW);

  (* keep *) reg [WIDTH-1:0] mem_r [0:DEPTH-1];

  // ---- write side ---------------------------------------------------------
  (* keep *) reg [AW:0] wbin_r, wgray_r;
  wire [AW:0] wbin_nxt  = wbin_r + { {AW{1'b0}}, (wpush & ~wfull) };
  wire [AW:0] wgray_nxt = (wbin_nxt >> 1) ^ wbin_nxt;

  always @(posedge wclk or negedge wrst_n) begin
    if (!wrst_n) begin
      wbin_r  <= {(AW+1){1'b0}};
      wgray_r <= {(AW+1){1'b0}};
    end else begin
      wbin_r  <= wbin_nxt;
      wgray_r <= wgray_nxt;
    end
  end

  always @(posedge wclk) begin
    if (wpush && !wfull) mem_r[wbin_r[AW-1:0]] <= wdata;
  end

  // ---- read side ----------------------------------------------------------
  (* keep *) reg [AW:0] rbin_r, rgray_r;
  wire [AW:0] rbin_nxt  = rbin_r + { {AW{1'b0}}, (rpop & ~rempty) };
  wire [AW:0] rgray_nxt = (rbin_nxt >> 1) ^ rbin_nxt;

  always @(posedge rclk or negedge rrst_n) begin
    if (!rrst_n) begin
      rbin_r  <= {(AW+1){1'b0}};
      rgray_r <= {(AW+1){1'b0}};
    end else begin
      rbin_r  <= rbin_nxt;
      rgray_r <= rgray_nxt;
    end
  end

  assign rdata = mem_r[rbin_r[AW-1:0]];

  // ---- pointer crossings --------------------------------------------------
  wire [AW:0] wgray_at_r, rgray_at_w;

  cdc_sync_2ff #(.WIDTH(AW+1)) u_w2r (
      .dst_clk(rclk), .dst_rst_n(rrst_n), .d_src(wgray_r), .q_dst(wgray_at_r));

  cdc_sync_2ff #(.WIDTH(AW+1)) u_r2w (
      .dst_clk(wclk), .dst_rst_n(wrst_n), .d_src(rgray_r), .q_dst(rgray_at_w));

  assign rempty = (rgray_r == wgray_at_r);
  assign wfull  = (wgray_r == {~rgray_at_w[AW:AW-1], rgray_at_w[AW-2:0]});

endmodule

`default_nettype wire
