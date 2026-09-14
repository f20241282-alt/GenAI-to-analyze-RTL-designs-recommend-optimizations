// ---------------------------------------------------------------------------
// fanout_hub -- one control register driving a very wide load
//
// OPTIMISATION TARGET (T4 resource_duplication)
//   mode_q drives FANOUT loads from a single flop. The net capacitance and
//   the resulting output slew dominate every path that starts at this
//   register. Duplicating the flop into N copies and splitting the loads is
//   latency-preserving and trivially provable by sequential induction.
// ---------------------------------------------------------------------------
`default_nettype none

module fanout_hub #(
    parameter integer FANOUT = 64
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire                 mode_set,
    input  wire [1:0]           mode_in,
    input  wire [FANOUT-1:0]    data_in,
    output reg  [FANOUT-1:0]    data_q
);

  // genrtl-target: high_fanout_reg name=mode_q loads=FANOUT
  (* keep *) reg [1:0] mode_q;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)        mode_q <= 2'd0;
    else if (mode_set) mode_q <= mode_in;
  end

  integer i;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      data_q <= {FANOUT{1'b0}};
    end else begin
      for (i = 0; i < FANOUT; i = i + 1) begin
        case (mode_q)
          2'd0: data_q[i] <=  data_in[i];
          2'd1: data_q[i] <= ~data_in[i];
          2'd2: data_q[i] <=  data_in[i] ^ data_in[(i+1) % FANOUT];
          default: data_q[i] <= data_in[i] & data_in[(i+3) % FANOUT];
        endcase
      end
    end
  end

endmodule

`default_nettype wire
