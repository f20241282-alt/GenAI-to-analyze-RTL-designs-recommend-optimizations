// ===========================================================================
// crypto_core.sv -- a keyed mixing/hash core, deliberately un-optimised.
//
//   python -m genrtl suggest examples/crypto_core.sv
//
// Exercises (among others):
//   - logic_restructure / combinational_depth : long XOR reduction chains
//   - strength_reduction                        : * / % by powers of two
//   - comparator_chain                          : OR of equality tests
//   - pipelining                                : an un-pipelined multiplier
//   - common_subexpression                      : a mixing term computed twice
// Elaborates cleanly under SV-2012.
// ===========================================================================
`default_nettype none

// ---------------------------------------------------------------------------
// mix_round -- one keyed mixing round over a 32-bit state.
// genrtl-latency-flex: 1
// ---------------------------------------------------------------------------
module mix_round (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        in_valid,
    input  wire [31:0] state,
    input  wire [31:0] key,
    output reg         out_valid,
    output reg  [31:0] hash_q
);
  // 8-term XOR chain (depth 7). The terms are not all simple operands, so the
  // verified logic_restructure declines and the advisor recommends balancing.
  wire [31:0] mixed_c = state ^ key ^
                        {state[15:0], state[31:16]} ^
                        (state << 3) ^ (state >> 5) ^
                        (key * 4) ^                       // strength reduction
                        (key + state) ^ 32'hDEAD_BEEF;

  // an un-pipelined multiplier feeding the round result
  wire [31:0] scr_c = (mixed_c * 32'd2654435761) ^ (key / 8);   // */ powers/const

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      hash_q    <= 32'd0;
      out_valid <= 1'b0;
    end else begin
      out_valid <= in_valid;
      if (in_valid) hash_q <= scr_c;
    end
  end
endmodule

// ---------------------------------------------------------------------------
// crypto_core -- selects one of four round variants and folds the result.
// ---------------------------------------------------------------------------
module crypto_core (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire [1:0]  mode,
    input  wire [31:0] data,
    input  wire [31:0] key,
    output reg  [31:0] digest_q,
    output reg         valid_q
);
  wire        r_valid;
  wire [31:0] r_hash;
  mix_round u_round (.clk(clk), .rst_n(rst_n), .in_valid(start),
                     .state(data), .key(key),
                     .out_valid(r_valid), .hash_q(r_hash));

  // full-cover 4-way select over `mode` -> balanced_mux_tree (AUTO).
  wire [31:0] sel_c = (mode == 2'd0) ? (r_hash)                :
                      (mode == 2'd1) ? (r_hash ^ key)          :
                      (mode == 2'd2) ? (r_hash + key)          :
                                       (r_hash & key);

  // OR of equality tests -> decode `mode` once.
  wire strong_c = (mode == 2'd1) | (mode == 2'd2) | (mode == 2'd3);

  // the folding term (sel_c ^ rot_c) is computed twice (common sub-expression).
  wire [31:0] rot_c  = {key[7:0], key[31:8]};
  wire [31:0] fold_a = (sel_c ^ rot_c) + 32'd1;
  wire [31:0] fold_b = (sel_c ^ rot_c) + 32'd2;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      digest_q <= 32'd0;
      valid_q  <= 1'b0;
    end else begin
      valid_q  <= r_valid;
      digest_q <= strong_c ? (fold_a ^ fold_b) : fold_a;
    end
  end
endmodule

`default_nettype wire
