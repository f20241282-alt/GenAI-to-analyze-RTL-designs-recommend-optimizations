// ---------------------------------------------------------------------------
// cdc_handshake -- four-phase req/ack data crossing with a held data bus
//
// genrtl-protect: cdc_handshake
//   PROTECTED. Data is captured on the destination side only while req/ack are
//   stable; any retiming of the data path breaks that stability contract.
// ---------------------------------------------------------------------------
`default_nettype none

(* genrtl_protect = "cdc_handshake" *)
(* keep_hierarchy *)
module cdc_handshake #(
    parameter integer WIDTH = 32
) (
    input  wire              src_clk,
    input  wire              src_rst_n,
    input  wire              src_valid,
    output wire              src_ready,
    input  wire [WIDTH-1:0]  src_data,

    input  wire              dst_clk,
    input  wire              dst_rst_n,
    output reg               dst_valid,
    input  wire              dst_ready,
    output reg  [WIDTH-1:0]  dst_data
);

  (* keep *) reg              req_r;
  (* keep *) reg              ack_r;
  (* keep *) reg [WIDTH-1:0]  hold_r;

  wire ack_sync;
  cdc_sync_2ff #(.WIDTH(1)) u_ack_sync (
      .dst_clk(src_clk), .dst_rst_n(src_rst_n), .d_src(ack_r), .q_dst(ack_sync));

  assign src_ready = ~req_r & ~ack_sync;

  always @(posedge src_clk or negedge src_rst_n) begin
    if (!src_rst_n) begin
      req_r  <= 1'b0;
      hold_r <= {WIDTH{1'b0}};
    end else if (src_valid && src_ready) begin
      req_r  <= 1'b1;
      hold_r <= src_data;
    end else if (ack_sync) begin
      req_r  <= 1'b0;
    end
  end

  wire req_sync;
  cdc_sync_2ff #(.WIDTH(1)) u_req_sync (
      .dst_clk(dst_clk), .dst_rst_n(dst_rst_n), .d_src(req_r), .q_dst(req_sync));

  always @(posedge dst_clk or negedge dst_rst_n) begin
    if (!dst_rst_n) begin
      ack_r     <= 1'b0;
      dst_valid <= 1'b0;
      dst_data  <= {WIDTH{1'b0}};
    end else begin
      if (req_sync && !ack_r) begin
        dst_data  <= hold_r;   // stable: req is high, source is frozen
        dst_valid <= 1'b1;
        ack_r     <= 1'b1;
      end else if (dst_valid && dst_ready) begin
        dst_valid <= 1'b0;
      end
      if (!req_sync) ack_r <= 1'b0;
    end
  end

endmodule

`default_nettype wire
