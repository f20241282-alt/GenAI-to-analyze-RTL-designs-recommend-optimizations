// ---------------------------------------------------------------------------
// ctrl_fsm -- 12-state sequencer, densely binary encoded
//
// OPTIMISATION TARGET (T5 fsm_reencode, T6 boolean_factor)
//   * Binary encoding forces a 4-bit comparator in front of every branch, so
//     the next-state cone is deep. One-hot encoding replaces each comparator
//     with a single bit read.
//   * The guard expressions below are written in expanded sum-of-products
//     form with an obvious common factor -- see the `busy_c` assignment.
//
// The baseline synthesis script runs `synth -nofsm`, i.e. Yosys is explicitly
// forbidden from re-encoding this FSM itself, so any improvement here is
// attributable to the optimiser and not to the synthesiser.
// ---------------------------------------------------------------------------
`default_nettype none

module ctrl_fsm (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        abort,
    input  wire        done_in,
    input  wire [7:0]  status,
    output reg  [3:0]  phase_q,
    output reg         busy_q,
    output reg         grant_q
);

  localparam [3:0] S_IDLE   = 4'd0,
                   S_ARM    = 4'd1,
                   S_FETCH  = 4'd2,
                   S_DEC    = 4'd3,
                   S_ISSUE  = 4'd4,
                   S_EXEC0  = 4'd5,
                   S_EXEC1  = 4'd6,
                   S_EXEC2  = 4'd7,
                   S_WB     = 4'd8,
                   S_FLUSH  = 4'd9,
                   S_ERR    = 4'd10,
                   S_HALT   = 4'd11;

  // genrtl-target: fsm_binary states=12 reg=phase_q
  (* keep *) reg [3:0] state_r;
  reg [3:0] state_c;

  always @(*) begin
    state_c = state_r;
    case (state_r)
      S_IDLE  : if (start)                      state_c = S_ARM;
      S_ARM   : if (abort)                      state_c = S_FLUSH;
                else if (status[0])             state_c = S_FETCH;
      S_FETCH : if (abort)                      state_c = S_FLUSH;
                else if (status[1])             state_c = S_DEC;
      S_DEC   : if (status[7])                  state_c = S_ERR;
                else                            state_c = S_ISSUE;
      S_ISSUE : if (abort)                      state_c = S_FLUSH;
                else                            state_c = S_EXEC0;
      S_EXEC0 :                                 state_c = S_EXEC1;
      S_EXEC1 :                                 state_c = S_EXEC2;
      S_EXEC2 : if (done_in)                    state_c = S_WB;
      S_WB    :                                 state_c = S_IDLE;
      S_FLUSH : if (status[2])                  state_c = S_IDLE;
      S_ERR   : if (status[3])                  state_c = S_HALT;
                else                            state_c = S_FLUSH;
      S_HALT  : if (!start)                     state_c = S_HALT;
                else                            state_c = S_IDLE;
      default :                                 state_c = S_IDLE;
    endcase
  end

  // phase_q is an *encoded* output, decoded from the state rather than being
  // the raw state vector. That is what makes re-encoding a legal internal
  // change: the module's observable behaviour does not depend on how the
  // states happen to be numbered.
  reg [3:0] phase_c;
  always @(*) begin
    case (state_c)
      S_IDLE  : phase_c = 4'd0;
      S_ARM   : phase_c = 4'd1;
      S_FETCH : phase_c = 4'd2;
      S_DEC   : phase_c = 4'd3;
      S_ISSUE : phase_c = 4'd4;
      S_EXEC0 : phase_c = 4'd5;
      S_EXEC1 : phase_c = 4'd6;
      S_EXEC2 : phase_c = 4'd7;
      S_WB    : phase_c = 4'd8;
      S_FLUSH : phase_c = 4'd9;
      S_ERR   : phase_c = 4'd10;
      S_HALT  : phase_c = 4'd11;
      default : phase_c = 4'd0;
    endcase
  end

  // genrtl-target: sop_common_factor var=in_flight
  wire in_flight = (state_r != S_IDLE) & (state_r != S_HALT);
  wire busy_c    = (in_flight & status[4]) |
                   (in_flight & status[5]) |
                   (in_flight & status[6]) |
                   (in_flight & done_in);

  wire grant_c = (state_r == S_ISSUE) | (state_r == S_EXEC0) |
                 (state_r == S_EXEC1) | (state_r == S_EXEC2);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_r <= S_IDLE;
      phase_q <= 4'd0;
      busy_q  <= 1'b0;
      grant_q <= 1'b0;
    end else begin
      state_r <= state_c;
      phase_q <= phase_c;
      busy_q  <= busy_c;
      grant_q <= grant_c;
    end
  end

endmodule

`default_nettype wire
