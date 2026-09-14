// ---------------------------------------------------------------------------
// nebula_top -- multi-clock timing-closure benchmark
//
//  * 5 independent asynchronous master clocks
//      clk_core  clk_dsp  clk_mem  clk_io  clk_sec
//  * 5 generated clocks produced by nebula_clkgen
//      /2  /3  /4  /5  /8   (multiple divide ratios, per spec)
//  * real CDC structures on every crossing
//      async FIFO x2, 4-phase handshake x2, toggle pulse sync x1, 2FF sync
//  * ~50 K standard cells in the FULL configuration
//
// Everything under rtl/cdc and rtl/clk is PROTECTED (do-not-touch). Everything
// else is fair game for the optimiser.
// ---------------------------------------------------------------------------
`default_nettype none
`include "nebula_defines.svh"

module nebula_top (
    input  wire         clk_core,
    input  wire         clk_dsp,
    input  wire         clk_mem,
    input  wire         clk_io,
    input  wire         clk_sec,

    input  wire         rst_core_n,
    input  wire         rst_dsp_n,
    input  wire         rst_mem_n,
    input  wire         rst_io_n,
    input  wire         rst_sec_n,

    input  wire         start,
    input  wire         abort,
    input  wire [7:0]   status,
    input  wire [3:0]   alu_op,
    input  wire [4:0]   rs1_addr,
    input  wire [4:0]   rs2_addr,
    input  wire [4:0]   rd_addr,
    input  wire         rd_we,
    input  wire [31:0]  din,
    input  wire [3:0]   dsp_sel,
    input  wire [127:0] aes_key,

    output wire [31:0]  core_out,
    output wire [31:0]  dsp_out,
    output wire [31:0]  mem_out,
    output wire [31:0]  io_out,
    output wire [127:0] sec_out,
    output wire [4:0]   div_out
);

  localparam integer DSP_LANES = `NEBULA_DSP_LANES;
  localparam integer AES_LANES = `NEBULA_AES_LANES;
  localparam integer CRC_LANES = `NEBULA_CRC_LANES;
  localparam integer RV_LANES  = `NEBULA_RV_LANES;
  localparam integer FIFO_AW   = `NEBULA_FIFO_AW;

  // =========================================================================
  // Clock generation (PROTECTED)
  // =========================================================================
  wire clk_core_div2, clk_dsp_div3, clk_mem_div4, clk_io_div5, clk_sec_div8;

  nebula_clkgen u_clkgen (
      .clk_core(clk_core), .clk_dsp(clk_dsp), .clk_mem(clk_mem),
      .clk_io(clk_io),     .clk_sec(clk_sec),
      .rst_core_n(rst_core_n), .rst_dsp_n(rst_dsp_n), .rst_mem_n(rst_mem_n),
      .rst_io_n(rst_io_n),     .rst_sec_n(rst_sec_n),
      .clk_core_div2(clk_core_div2), .clk_dsp_div3(clk_dsp_div3),
      .clk_mem_div4(clk_mem_div4),   .clk_io_div5(clk_io_div5),
      .clk_sec_div8(clk_sec_div8));

  // =========================================================================
  // clk_core domain : RV lanes + high-fanout control hub
  // =========================================================================
  wire [31:0] rv_res [0:RV_LANES-1];
  wire        rv_zero [0:RV_LANES-1];
  genvar gr;
  generate
    for (gr = 0; gr < RV_LANES; gr = gr + 1) begin : g_rv
      rv_lane #(.LANE_ID(gr)) u_lane (
          .clk(clk_core), .rst_n(rst_core_n),
          .alu_op(alu_op), .rs1_addr(rs1_addr), .rs2_addr(rs2_addr),
          .rd_addr(rd_addr), .rd_we(rd_we), .din(din),
          .result(rv_res[gr]), .zero(rv_zero[gr]));
    end
  endgenerate

  wire [63:0] hub_q;
  fanout_hub #(.FANOUT(64)) u_hub (
      .clk(clk_core), .rst_n(rst_core_n),
      .mode_set(start), .mode_in(status[1:0]),
      .data_in({din, din ^ rv_res[0]}), .data_q(hub_q));

  reg  [31:0] core_acc_r;
  reg  [31:0] core_mix;
  integer ci;
  always @(*) begin
    core_mix = hub_q[63:32] ^ hub_q[31:0];
    for (ci = 0; ci < RV_LANES; ci = ci + 1)
      core_mix = core_mix ^ rv_res[ci] ^ {31'd0, rv_zero[ci]};
  end
  always @(posedge clk_core or negedge rst_core_n) begin
    if (!rst_core_n) core_acc_r <= 32'd0;
    else             core_acc_r <= core_mix;
  end
  assign core_out = core_acc_r;

  // =========================================================================
  // CDC : clk_core -> clk_dsp  (asynchronous FIFO)
  // =========================================================================
  wire        c2d_full, c2d_empty;
  wire [31:0] c2d_data;

  cdc_async_fifo #(.WIDTH(32), .AW(FIFO_AW)) u_fifo_c2d (
      .wclk(clk_core), .wrst_n(rst_core_n),
      .wpush(~c2d_full), .wdata(core_acc_r), .wfull(c2d_full),
      .rclk(clk_dsp),  .rrst_n(rst_dsp_n),
      .rpop(~c2d_empty), .rdata(c2d_data), .rempty(c2d_empty));

  // =========================================================================
  // clk_dsp domain : DSP lanes
  // =========================================================================
  // register the FIFO read data so DSP lanes start from a clean flop
  reg [31:0] dsp_seed_r;
  reg        dsp_seed_v_r;
  always @(posedge clk_dsp or negedge rst_dsp_n) begin
    if (!rst_dsp_n) begin
      dsp_seed_r   <= 32'd0;
      dsp_seed_v_r <= 1'b0;
    end else begin
      dsp_seed_r   <= c2d_data;
      dsp_seed_v_r <= ~c2d_empty;
    end
  end

  wire [31:0] dsp_res [0:DSP_LANES-1];
  wire        dsp_v   [0:DSP_LANES-1];
  genvar gd;
  generate
    for (gd = 0; gd < DSP_LANES; gd = gd + 1) begin : g_dsp
      dsp_lane #(.LANE_ID(gd)) u_lane (
          .clk(clk_dsp), .rst_n(rst_dsp_n), .en(1'b1),
          .sel(dsp_sel ^ gd[3:0]),
          .seed(dsp_seed_r),
          .in_valid(dsp_seed_v_r),
          .out_valid(dsp_v[gd]),
          .result(dsp_res[gd]));
    end
  endgenerate

  reg  [31:0] dsp_acc_r;
  reg  [31:0] dsp_mix;
  integer di;
  always @(*) begin
    dsp_mix = 32'd0;
    for (di = 0; di < DSP_LANES; di = di + 1)
      dsp_mix = dsp_mix ^ (dsp_v[di] ? dsp_res[di] : 32'd0);
  end
  always @(posedge clk_dsp or negedge rst_dsp_n) begin
    if (!rst_dsp_n) dsp_acc_r <= 32'd0;
    else            dsp_acc_r <= dsp_mix;
  end
  assign dsp_out = dsp_acc_r;

  // =========================================================================
  // CDC : clk_dsp -> clk_mem  (4-phase handshake)
  // =========================================================================
  wire        d2m_ready, d2m_valid;
  wire [31:0] d2m_data;

  cdc_handshake #(.WIDTH(32)) u_hs_d2m (
      .src_clk(clk_dsp), .src_rst_n(rst_dsp_n),
      .src_valid(1'b1), .src_ready(d2m_ready), .src_data(dsp_acc_r),
      .dst_clk(clk_mem), .dst_rst_n(rst_mem_n),
      .dst_valid(d2m_valid), .dst_ready(1'b1), .dst_data(d2m_data));

  // =========================================================================
  // clk_mem domain : control FSM
  // =========================================================================
  wire [3:0] fsm_phase;
  wire       fsm_busy, fsm_grant;

  ctrl_fsm u_fsm (
      .clk(clk_mem), .rst_n(rst_mem_n),
      .start(start), .abort(abort), .done_in(d2m_valid),
      .status(status ^ d2m_data[7:0]),
      .phase_q(fsm_phase), .busy_q(fsm_busy), .grant_q(fsm_grant));

  reg [31:0] mem_acc_r;
  always @(posedge clk_mem or negedge rst_mem_n) begin
    if (!rst_mem_n) mem_acc_r <= 32'd0;
    else            mem_acc_r <= {d2m_data[27:0], fsm_phase} ^
                                 {30'd0, fsm_busy, fsm_grant};
  end
  assign mem_out = mem_acc_r;

  // =========================================================================
  // CDC : clk_mem -> clk_io  (toggle pulse synchroniser)
  // =========================================================================
  wire m2i_pulse;
  cdc_pulse_sync u_ps_m2i (
      .src_clk(clk_mem), .src_rst_n(rst_mem_n), .src_pulse(fsm_grant),
      .dst_clk(clk_io),  .dst_rst_n(rst_io_n),  .dst_pulse(m2i_pulse));

  // =========================================================================
  // clk_io domain : CRC engines
  // =========================================================================
  reg [31:0] io_din_r;
  always @(posedge clk_io or negedge rst_io_n) begin
    if (!rst_io_n) io_din_r <= 32'd0;
    else           io_din_r <= din;
  end

  wire [31:0] crc_res [0:CRC_LANES-1];
  genvar gc;
  generate
    for (gc = 0; gc < CRC_LANES; gc = gc + 1) begin : g_crc
      crc32_par u_crc (
          .clk(clk_io), .rst_n(rst_io_n),
          .clear(abort), .valid(m2i_pulse | (gc != 0)),
          .data(io_din_r ^ {28'd0, gc[3:0]}),
          .crc_q(crc_res[gc]));
    end
  endgenerate

  reg  [31:0] io_acc_r;
  reg  [31:0] io_mix;
  integer ii;
  always @(*) begin
    io_mix = 32'd0;
    for (ii = 0; ii < CRC_LANES; ii = ii + 1)
      io_mix = io_mix ^ crc_res[ii];
  end
  always @(posedge clk_io or negedge rst_io_n) begin
    if (!rst_io_n) io_acc_r <= 32'd0;
    else           io_acc_r <= io_mix;
  end
  assign io_out = io_acc_r;

  // =========================================================================
  // CDC : clk_io -> clk_sec  (asynchronous FIFO)
  // =========================================================================
  wire        i2s_full, i2s_empty;
  wire [31:0] i2s_data;

  cdc_async_fifo #(.WIDTH(32), .AW(FIFO_AW)) u_fifo_i2s (
      .wclk(clk_io),  .wrst_n(rst_io_n),
      .wpush(~i2s_full), .wdata(io_acc_r), .wfull(i2s_full),
      .rclk(clk_sec), .rrst_n(rst_sec_n),
      .rpop(~i2s_empty), .rdata(i2s_data), .rempty(i2s_empty));

  // =========================================================================
  // clk_sec domain : AES round engines
  // =========================================================================
  wire [127:0] aes_res [0:AES_LANES-1];
  wire         aes_v   [0:AES_LANES-1];
  reg  [127:0] aes_state_r;

  always @(posedge clk_sec or negedge rst_sec_n) begin
    if (!rst_sec_n) aes_state_r <= 128'd0;
    else            aes_state_r <= {aes_state_r[95:0], i2s_data};
  end

  genvar ga;
  generate
    for (ga = 0; ga < AES_LANES; ga = ga + 1) begin : g_aes
      aes_round #(.ROUNDS(`NEBULA_AES_ROUNDS)) u_round (
          .clk(clk_sec), .rst_n(rst_sec_n),
          .in_valid(~i2s_empty),
          .state_in(aes_state_r ^ {124'd0, ga[3:0]}),
          .rkey0(aes_key),
          .rkey1(~aes_key),
          .out_valid(aes_v[ga]),
          .state_q(aes_res[ga]));
    end
  endgenerate

  reg  [127:0] sec_acc_r;
  reg  [127:0] sec_mix;
  integer si;
  always @(*) begin
    sec_mix = 128'd0;
    for (si = 0; si < AES_LANES; si = si + 1)
      sec_mix = sec_mix ^ (aes_v[si] ? aes_res[si] : 128'd0);
  end
  always @(posedge clk_sec or negedge rst_sec_n) begin
    if (!rst_sec_n) sec_acc_r <= 128'd0;
    else            sec_acc_r <= sec_mix;
  end
  assign sec_out = sec_acc_r;

  // =========================================================================
  // CDC : clk_sec -> clk_core  (4-phase handshake, closes the ring)
  // =========================================================================
  wire        s2c_valid;
  wire [31:0] s2c_data;
  cdc_handshake #(.WIDTH(32)) u_hs_s2c (
      .src_clk(clk_sec),  .src_rst_n(rst_sec_n),
      .src_valid(1'b1), .src_ready(), .src_data(sec_acc_r[31:0]),
      .dst_clk(clk_core), .dst_rst_n(rst_core_n),
      .dst_valid(s2c_valid), .dst_ready(1'b1), .dst_data(s2c_data));

  // =========================================================================
  // Generated-clock domains: small accumulators so the /2 /3 /4 /5 /8 clocks
  // own real registers and therefore real timing endpoints.
  // =========================================================================
  reg [7:0] div_core_r, div_dsp_r, div_mem_r, div_io_r, div_sec_r;

  always @(posedge clk_core_div2 or negedge rst_core_n)
    if (!rst_core_n) div_core_r <= 8'd0; else div_core_r <= div_core_r + s2c_data[7:0];
  always @(posedge clk_dsp_div3 or negedge rst_dsp_n)
    if (!rst_dsp_n)  div_dsp_r  <= 8'd0; else div_dsp_r  <= div_dsp_r  + dsp_acc_r[7:0];
  always @(posedge clk_mem_div4 or negedge rst_mem_n)
    if (!rst_mem_n)  div_mem_r  <= 8'd0; else div_mem_r  <= div_mem_r  + mem_acc_r[7:0];
  always @(posedge clk_io_div5 or negedge rst_io_n)
    if (!rst_io_n)   div_io_r   <= 8'd0; else div_io_r   <= div_io_r   + io_acc_r[7:0];
  always @(posedge clk_sec_div8 or negedge rst_sec_n)
    if (!rst_sec_n)  div_sec_r  <= 8'd0; else div_sec_r  <= div_sec_r  + sec_acc_r[7:0];

  // 2FF synchroniser from a divided domain back into clk_mem (PROTECTED)
  wire [7:0] div_core_at_mem;
  cdc_sync_2ff #(.WIDTH(8)) u_sync_div (
      .dst_clk(clk_mem), .dst_rst_n(rst_mem_n),
      .d_src(div_core_r), .q_dst(div_core_at_mem));

  assign div_out = { ^div_core_at_mem, ^div_dsp_r, ^div_mem_r, ^div_io_r, ^div_sec_r };

endmodule

`default_nettype wire
