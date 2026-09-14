// ---------------------------------------------------------------------------
// aes_round -- ROUNDS AES rounds chained inside a single clock cycle
//
// OPTIMISATION TARGET (T3 pipeline_insert)
//   With ROUNDS=2, SubBytes -> ShiftRows -> MixColumns -> AddRoundKey runs
//   twice back-to-back before the state register, which is roughly twice the
//   depth the clk_sec period can absorb. Inserting a register between round 0
//   and round 1 halves the path at the cost of one cycle of latency.
//
// genrtl-latency-flex: 1
//   The consumer is valid-qualified, so one additional pipeline stage is legal
//   provided out_valid is delayed by the same amount. The formal gate proves
//   this with a latency-offset sequential miter rather than assuming it.
//
// genrtl-latency-stable: rkey0, rkey1
//   Round keys are consumed by different rounds. Once the rounds sit in
//   different cycles, round 1 reads its key one cycle after round 0 does, so
//   the keys must be stable while a block is in flight. nebula_top drives them
//   from a static primary input, which satisfies this. The formal gate encodes
//   the requirement as an explicit assumption -- it does not silently ignore
//   it, and without the annotation the pipelining proof correctly fails.
// ---------------------------------------------------------------------------
`default_nettype none

module aes_round #(
    parameter integer ROUNDS = 2
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire         in_valid,
    input  wire [127:0] state_in,
    input  wire [127:0] rkey0,
    input  wire [127:0] rkey1,
    output reg          out_valid,
    output reg  [127:0] state_q
);

  function automatic [7:0] xtime;
    input [7:0] b;
    begin
      xtime = b[7] ? ((b << 1) ^ 8'h1B) : (b << 1);
    end
  endfunction

  wire [127:0] rk [0:1];
  assign rk[0] = rkey0;
  assign rk[1] = rkey1;

  wire [127:0] chain [0:ROUNDS];
  // >>> genrtl-pipeline-stage 1: inserted by the GenAI optimiser
  localparam integer GENRTL_PIPE_AT_P1 = (ROUNDS)/2;
  reg [127:0] genrtl_pipe_p1;
  reg genrtl_valid_p1;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      genrtl_pipe_p1 <= '0;
      genrtl_valid_p1 <= 1'b0;
    end else begin
      genrtl_valid_p1 <= in_valid;
      if (in_valid) genrtl_pipe_p1 <= chain[GENRTL_PIPE_AT_P1];
    end
  end
  wire [127:0] chain_genrtl_s1 [0:ROUNDS];
  genvar genrtl_gp1;
  generate
    for (genrtl_gp1 = 0; genrtl_gp1 <= ROUNDS; genrtl_gp1 = genrtl_gp1 + 1) begin : g_chain_genrtl_s1
      if (genrtl_gp1 == GENRTL_PIPE_AT_P1) assign chain_genrtl_s1[genrtl_gp1] = genrtl_pipe_p1;
      else              assign chain_genrtl_s1[genrtl_gp1] = chain[genrtl_gp1];
    end
  endgenerate
  // <<< genrtl-pipeline-stage 1

  assign chain[0] = state_in;

  genvar gr, gb, gc;
  generate
    for (gr = 0; gr < ROUNDS; gr = gr + 1) begin : g_round
      wire [127:0] sub, shf, mix;

      // ---- SubBytes ------------------------------------------------------
      for (gb = 0; gb < 16; gb = gb + 1) begin : g_sub
        aes_sbox u_sb (.din(chain_genrtl_s1[gr][gb*8 +: 8]), .dout(sub[gb*8 +: 8]));
      end

      // ---- ShiftRows (column-major 4x4 byte state) ------------------------
      for (gb = 0; gb < 16; gb = gb + 1) begin : g_shf
        localparam integer RR = gb % 4;
        localparam integer CC = gb / 4;
        localparam integer SC = (CC + RR) % 4;
        assign shf[gb*8 +: 8] = sub[(SC*4 + RR)*8 +: 8];
      end

      // ---- MixColumns ------------------------------------------------------
      for (gc = 0; gc < 4; gc = gc + 1) begin : g_mix
        wire [7:0] b0 = shf[(gc*4+0)*8 +: 8];
        wire [7:0] b1 = shf[(gc*4+1)*8 +: 8];
        wire [7:0] b2 = shf[(gc*4+2)*8 +: 8];
        wire [7:0] b3 = shf[(gc*4+3)*8 +: 8];
        assign mix[(gc*4+0)*8 +: 8] = xtime(b0) ^ (xtime(b1) ^ b1) ^ b2 ^ b3;
        assign mix[(gc*4+1)*8 +: 8] = b0 ^ xtime(b1) ^ (xtime(b2) ^ b2) ^ b3;
        assign mix[(gc*4+2)*8 +: 8] = b0 ^ b1 ^ xtime(b2) ^ (xtime(b3) ^ b3);
        assign mix[(gc*4+3)*8 +: 8] = (xtime(b0) ^ b0) ^ b1 ^ b2 ^ xtime(b3);
      end

      // ---- AddRoundKey -----------------------------------------------------
      assign chain[gr+1] = mix ^ rk[gr % 2];
    end
  endgenerate

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state_q   <= 128'd0;
      out_valid <= 1'b0;
    end else begin
      out_valid <= genrtl_valid_p1;
      if (genrtl_valid_p1) state_q <= chain_genrtl_s1[ROUNDS];
    end
  end

endmodule

`default_nettype wire
