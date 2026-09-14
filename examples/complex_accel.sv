// ===========================================================================
// complex_accel.sv -- a deliberately UN-optimised accelerator, for testing.
//
// This is a self-contained, multi-module design that is NOT part of the Nebula
// benchmark. It exists to give the optimisation advisor plenty to chew on:
//
//   python -m genrtl suggest examples/complex_accel.sv
//   python -m genrtl suggest examples/complex_accel.sv --format md --out review.md
//
// It packs in, on purpose, at least one instance of almost every category the
// advisor knows about:
//   - balanced_mux_tree  (AUTO)   : 8-way priority select over a full code space
//   - balanced_adder_tree(AUTO)   : long '+' reduction chains
//   - fsm_reencode       (AUTO)   : a dense 10-state FSM
//   - boolean_factor     (AUTO)   : a sum-of-products with a common term
//   - resource_duplication(AUTO)  : a high-fanout mode register
//   - pipelining (advisory)       : an un-pipelined multiply/MAC cloud
//   - strength_reduction (advisory): * / % by powers of two
//   - comparator_chain   (advisory): OR of equality tests
//   - latch_inference    (advisory): a combinational case with no default
//   - blocking_in_sequential (advisory, HIGH): '=' in a clocked block
//   - cdc_multibit       (advisory): a 16-bit bus sampled by an unrelated clock
//
// It uses two asynchronous clocks (clk_a, clk_b) so the CDC check has something
// real to find. It elaborates cleanly under Yosys / any SV-2012 tool.
// ===========================================================================
`default_nettype none

// ---------------------------------------------------------------------------
// accel_alu -- result chosen by an 8-deep priority chain (really a mux tree).
// ---------------------------------------------------------------------------
module accel_alu (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [2:0]  op,
    input  wire [31:0] a,
    input  wire [31:0] b,
    output reg  [31:0] y_q,
    output reg         active_q
);
  // Every code of the 3-bit `op` is covered, so this priority chain has no
  // priority left to preserve -> balanced_mux_tree (AUTO). It also hides two
  // strength-reduction opportunities (`a * 8`, `a / 4`).
  wire [31:0] y_c = (op == 3'd0) ? (a + b)          :
                    (op == 3'd1) ? (a - b)          :
                    (op == 3'd2) ? (a & b)          :
                    (op == 3'd3) ? (a | b)          :
                    (op == 3'd4) ? (a ^ b)          :
                    (op == 3'd5) ? (a * 8)          :   // -> a << 3
                    (op == 3'd6) ? (a / 4)          :   // -> a >> 2 (unsigned)
                                   (b);

  // OR of equality tests -> decode `op` once instead of three comparators.
  wire active_c = (op == 3'd0) | (op == 3'd1) | (op == 3'd5);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      y_q      <= 32'd0;
      active_q <= 1'b0;
    end else begin
      y_q      <= y_c;
      active_q <= active_c;
    end
  end
endmodule

// ---------------------------------------------------------------------------
// accel_mac -- two multiplies and a 4-term add in one combinational cloud.
//
// genrtl-latency-flex: 2
//   The consumer is valid-qualified, so up to two pipeline stages are legal;
//   this contract lets the verified pipeline_insert transform apply here.
// ---------------------------------------------------------------------------
module accel_mac #(
    parameter integer W = 16
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              in_valid,
    input  wire [W-1:0]      a,
    input  wire [W-1:0]      b,
    input  wire [W-1:0]      c,
    input  wire [W-1:0]      d,
    output reg               out_valid,
    output reg  [2*W+1:0]    acc_q
);
  wire [2*W-1:0] p0 = a * b;                 // un-pipelined multipliers
  wire [2*W-1:0] p1 = c * d;
  // 4-term left-associative add chain (depth 3 -> balanced depth 2).
  wire [2*W+1:0] sum_c = p0 + p1 + {a, 1'b0} + {d, 1'b0};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc_q     <= '0;
      out_valid <= 1'b0;
    end else begin
      out_valid <= in_valid;
      if (in_valid) acc_q <= sum_c;
    end
  end
endmodule

// ---------------------------------------------------------------------------
// accel_fsm -- a 10-state, densely binary-encoded sequencer.
// ---------------------------------------------------------------------------
module accel_fsm (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       go,
    input  wire       kill,
    input  wire       ack,
    input  wire [7:0] flags,
    output reg  [3:0] phase_q,
    output reg        run_q
);
  localparam [3:0] S_IDLE = 4'd0, S_LOAD = 4'd1, S_ADDR = 4'd2, S_READ = 4'd3,
                   S_EXEC = 4'd4, S_MUL  = 4'd5, S_ACC  = 4'd6, S_WRITE = 4'd7,
                   S_DONE = 4'd8, S_ERR  = 4'd9;

  reg [3:0] state_r, state_c;

  always @(*) begin
    state_c = state_r;
    case (state_r)
      S_IDLE : if (go)          state_c = S_LOAD;
      S_LOAD : if (kill)        state_c = S_ERR;
               else             state_c = S_ADDR;
      S_ADDR :                  state_c = S_READ;
      S_READ : if (flags[0])    state_c = S_EXEC;
      S_EXEC : if (flags[1])    state_c = S_MUL;
               else             state_c = S_ACC;
      S_MUL  :                  state_c = S_ACC;
      S_ACC  : if (ack)         state_c = S_WRITE;
      S_WRITE:                  state_c = S_DONE;
      S_DONE :                  state_c = S_IDLE;
      S_ERR  : if (!go)         state_c = S_ERR;
               else             state_c = S_IDLE;
      default:                  state_c = S_IDLE;
    endcase
  end

  // Combinational phase decode with NO default -> infers a latch on phase_c.
  reg [3:0] phase_c;
  always @(*) begin
    case (state_r)
      S_LOAD : phase_c = 4'd1;
      S_EXEC : phase_c = 4'd2;
      S_MUL  : phase_c = 4'd3;
      S_ACC  : phase_c = 4'd4;
      S_WRITE: phase_c = 4'd5;
      // no default: phase_c latches in the other states
    endcase
  end

  // Sum of products sharing the common factor `busy`.
  wire busy  = (state_r != S_IDLE) & (state_r != S_DONE) & (state_r != S_ERR);
  wire run_c = (busy & flags[2]) | (busy & flags[3]) | (busy & ack);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_r <= S_IDLE;
      phase_q <= 4'd0;
      run_q   <= 1'b0;
    end else begin
      state_r <= state_c;
      phase_q <= phase_c;
      run_q   <= run_c;
    end
  end
endmodule

// ---------------------------------------------------------------------------
// accel_top -- ties it together across TWO asynchronous clocks.
// ---------------------------------------------------------------------------
module accel_top (
    input  wire         clk_a,        // producer clock
    input  wire         clk_b,        // consumer clock (asynchronous to clk_a)
    input  wire         rst_n,
    input  wire         go,
    input  wire         kill,
    input  wire         ack,
    input  wire [2:0]   op,
    input  wire [7:0]   flags,
    input  wire [31:0]  din,
    input  wire [15:0]  sample,
    output wire [31:0]  alu_out,
    output reg  [31:0]  result_q,
    output wire [3:0]   phase_out,
    output reg          xfer_flag_q
);
  // ---- clk_a domain --------------------------------------------------------
  wire [31:0] alu_y;
  wire        alu_active;
  accel_alu u_alu (.clk(clk_a), .rst_n(rst_n), .op(op),
                   .a(din), .b({16'd0, sample}),
                   .y_q(alu_y), .active_q(alu_active));
  assign alu_out = alu_y;

  wire [33:0] mac_acc;
  wire        mac_v;
  accel_mac #(.W(16)) u_mac (
      .clk(clk_a), .rst_n(rst_n), .in_valid(alu_active),
      .a(sample), .b(din[15:0]), .c(din[31:16]), .d(sample),
      .out_valid(mac_v), .acc_q(mac_acc));

  // A high-fanout mode register: read all over the clk_a logic below.
  reg [7:0] mode_r;
  always @(posedge clk_a or negedge rst_n) begin
    if (!rst_n) mode_r <= 8'd0;
    else        mode_r <= din[7:0] ^ {7'd0, go};
  end

  // 8-term add reduction chain (depth 7 -> balanced depth 3).
  wire [31:0] mix_c = din + alu_y + mac_acc[31:0] +
                      {24'd0, mode_r} + {28'd0, op, go} +
                      {16'd0, sample} + {31'd0, alu_active} + 32'd7;

  // A 16-bit bus captured in clk_a...
  reg [15:0] capture_r;
  always @(posedge clk_a or negedge rst_n) begin
    if (!rst_n) capture_r <= 16'd0;
    else        capture_r <= sample ^ mix_c[15:0] ^ {8'd0, mode_r};
  end

  // ---- clk_b domain --------------------------------------------------------
  wire [3:0] fsm_phase;
  wire       fsm_run;
  accel_fsm u_fsm (.clk(clk_b), .rst_n(rst_n), .go(go), .kill(kill), .ack(ack),
                   .flags(flags), .phase_q(fsm_phase), .run_q(fsm_run));
  assign phase_out = fsm_phase;

  // ...and sampled directly by clk_b with a single bare flop.
  // This is a MULTI-BIT CDC HAZARD: the 16 bits can resolve on different edges.
  reg [15:0] synced_r;
  always @(posedge clk_b or negedge rst_n) begin
    if (!rst_n) synced_r <= 16'd0;
    else        synced_r <= capture_r;          // <-- advisor flags cdc_multibit
  end

  always @(posedge clk_b or negedge rst_n) begin
    if (!rst_n) begin
      result_q    <= 32'd0;
      xfer_flag_q <= 1'b0;
    end else begin
      result_q    <= {12'd0, synced_r, fsm_phase} ^ {28'd0, fsm_run, 3'd0};
      xfer_flag_q = fsm_run;                     // BUG: blocking '=' in a clocked block
    end
  end
endmodule

`default_nettype wire
