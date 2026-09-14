// ===========================================================================
// bus_arbiter.sv -- a 4-master arbiter + scaling datapath, un-optimised.
//
//   python -m genrtl suggest examples/bus_arbiter.sv
//
// Exercises (among others):
//   - fsm_reencode (AUTO)          : a dense arbiter FSM
//   - resource_sharing             : two multipliers in exclusive branches
//   - common_subexpression         : an address base computed twice
//   - latch_inference              : a combinational case with no default
//   - nonblocking_in_combinational : a '<=' inside always @(*)   (BUG)
//   - incomplete_sensitivity       : always @(req) on combinational logic (BUG)
//   - comparator_chain / strength_reduction
// Elaborates cleanly under SV-2012.
// ===========================================================================
`default_nettype none

module bus_arbiter (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  req,          // one bit per master
    input  wire        pick,         // datapath operand select
    input  wire [31:0] base,
    input  wire [31:0] offset,
    input  wire [15:0] a,
    input  wire [15:0] b,
    input  wire [15:0] coef,
    output reg  [3:0]  grant_q,
    output reg  [1:0]  state_q,
    output reg  [63:0] scaled_q,
    output reg  [31:0] addr_q
);
  // ---- dense-encoded round-robin FSM --------------------------------------
  localparam [2:0] A_IDLE = 3'd0, A_M0 = 3'd1, A_M1 = 3'd2, A_M2 = 3'd3,
                   A_M3 = 3'd4, A_HOLD = 3'd5, A_PARK = 3'd6;

  reg [2:0] astate_r, astate_c;
  always @(*) begin
    astate_c = astate_r;
    case (astate_r)
      A_IDLE : if (req[0])       astate_c = A_M0;
               else if (req[1])  astate_c = A_M1;
               else if (req[2])  astate_c = A_M2;
               else if (req[3])  astate_c = A_M3;
      A_M0   :                   astate_c = A_HOLD;
      A_M1   :                   astate_c = A_HOLD;
      A_M2   :                   astate_c = A_HOLD;
      A_M3   :                   astate_c = A_HOLD;
      A_HOLD : if (!(|req))      astate_c = A_PARK;
               else             astate_c = A_IDLE;
      A_PARK : if (|req)         astate_c = A_IDLE;
      default:                   astate_c = A_IDLE;
    endcase
  end

  // ---- grant decode with NO default -> latch on grant_c -------------------
  reg [3:0] grant_c;
  always @(*) begin
    case (astate_r)
      A_M0 : grant_c = 4'b0001;
      A_M1 : grant_c = 4'b0010;
      A_M2 : grant_c = 4'b0100;
      A_M3 : grant_c = 4'b1000;
      // no default: grant_c latches in IDLE/HOLD/PARK
    endcase
  end

  // ---- an intentionally-combinational block written with '<=' (BUG) -------
  //      and an incomplete sensitivity list (BUG): only `req` is listed.
  reg any_req_c;
  always @(req) begin
    any_req_c <= |req;               // BUG: non-blocking in combinational logic
  end

  // ---- scaling datapath: two multipliers in mutually exclusive branches ---
  //      (resource sharing), plus a shared address base (CSE) and a
  //      strength-reduction (offset * 2).
  wire [63:0] scaled_c = pick ? (a * coef) : (b * coef);
  wire [31:0] addr0_c  = (base + offset) + 32'd0;
  wire [31:0] addr1_c  = (base + offset) + (offset * 2);   // offset<<1
  wire [31:0] addr_c   = any_req_c ? addr1_c : addr0_c;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      astate_r <= A_IDLE;
      grant_q  <= 4'd0;
      state_q  <= 2'd0;
      scaled_q <= 64'd0;
      addr_q   <= 32'd0;
    end else begin
      astate_r <= astate_c;
      grant_q  <= grant_c;
      state_q  <= astate_r[1:0];
      scaled_q <= scaled_c;
      addr_q   <= addr_c;
    end
  end
endmodule

`default_nettype wire
