// ---------------------------------------------------------------------------
// vending_fsm -- an 8-state controller, densely binary encoded.
//
// This file is NOT part of the Nebula benchmark; it is a target for the
// standalone advisor. Opportunities the advisor is expected to flag:
//
//   * an 8-state binary FSM: every branch guard is a 3-bit comparator, so
//     one-hot re-encoding shortens the next-state cone   (fsm_reencode)
//   * a combinational `case` with no `default`, which can infer a latch on the
//     output register `disp_c`                            (lint: latch risk)
//   * a sum-of-products guard sharing a common term       (boolean_factor)
// ---------------------------------------------------------------------------
`default_nettype none

module vending_fsm (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       coin,
    input  wire       cancel,
    input  wire       dispense_ok,
    input  wire [3:0] level,
    output reg  [3:0] disp_q,
    output reg        vend_q
);

  localparam [2:0] S_IDLE = 3'd0,
                   S_5C    = 3'd1,
                   S_10C   = 3'd2,
                   S_15C   = 3'd3,
                   S_20C   = 3'd4,
                   S_VEND  = 3'd5,
                   S_CANCEL= 3'd6,
                   S_EMPTY = 3'd7;

  reg [2:0] state_r, state_c;

  always @(*) begin
    state_c = state_r;
    case (state_r)
      S_IDLE  : if (coin)          state_c = S_5C;
      S_5C    : if (cancel)        state_c = S_CANCEL;
                else if (coin)     state_c = S_10C;
      S_10C   : if (cancel)        state_c = S_CANCEL;
                else if (coin)     state_c = S_15C;
      S_15C   : if (cancel)        state_c = S_CANCEL;
                else if (coin)     state_c = S_20C;
      S_20C   : if (dispense_ok)   state_c = S_VEND;
                else               state_c = S_EMPTY;
      S_VEND  :                    state_c = S_IDLE;
      S_CANCEL:                    state_c = S_IDLE;
      S_EMPTY : if (!coin)         state_c = S_EMPTY;
                else               state_c = S_IDLE;
      default :                    state_c = S_IDLE;
    endcase
  end

  // Combinational decode with NO default -> disp_c is not assigned in every
  // branch, which infers a latch. The advisor flags this.
  reg [3:0] disp_c;
  always @(*) begin
    case (state_r)
      S_5C  : disp_c = 4'd1;
      S_10C : disp_c = 4'd2;
      S_15C : disp_c = 4'd3;
      S_20C : disp_c = 4'd4;
      S_VEND: disp_c = 4'd8;
      // no default, no S_IDLE/S_CANCEL/S_EMPTY: latch inferred on disp_c
    endcase
  end

  // Sum of products sharing the common factor `armed`.
  wire armed  = (state_r != S_IDLE) & (state_r != S_EMPTY);
  wire vend_c = (armed & dispense_ok) | (armed & level[0]) | (armed & level[1]);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_r <= S_IDLE;
      disp_q  <= 4'd0;
      vend_q  <= 1'b0;
    end else begin
      state_r <= state_c;
      disp_q  <= disp_c;
      vend_q  <= vend_c;
    end
  end

endmodule

`default_nettype wire
