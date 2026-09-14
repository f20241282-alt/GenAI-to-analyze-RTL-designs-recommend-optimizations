// ---------------------------------------------------------------------------
// nebula_clkgen -- generated-clock / clock-divider block
//
// genrtl-protect: clock_generation
//   This module is PROTECTED. The optimiser must never emit a patch that
//   touches any line of this file. Divider ratios define the generated clocks
//   declared in the SDC; restructuring them silently changes the timing model.
//
// Five independent asynchronous master clocks each drive one divider:
//   clk_core /2   clk_dsp /3   clk_mem /4   clk_io /5   clk_sec /8
// ---------------------------------------------------------------------------
`default_nettype none

(* genrtl_protect = "clock_generation" *)
(* keep_hierarchy *)
module nebula_clkgen (
    input  wire  clk_core,
    input  wire  clk_dsp,
    input  wire  clk_mem,
    input  wire  clk_io,
    input  wire  clk_sec,
    input  wire  rst_core_n,
    input  wire  rst_dsp_n,
    input  wire  rst_mem_n,
    input  wire  rst_io_n,
    input  wire  rst_sec_n,
    // generated clocks
    output wire  clk_core_div2,
    output wire  clk_dsp_div3,
    output wire  clk_mem_div4,
    output wire  clk_io_div5,
    output wire  clk_sec_div8
);

  // ---- /2 : simple toggle -------------------------------------------------
  (* keep *) reg clk_core_div2_r;
  always @(posedge clk_core or negedge rst_core_n) begin
    if (!rst_core_n) clk_core_div2_r <= 1'b0;
    else             clk_core_div2_r <= ~clk_core_div2_r;
  end
  assign clk_core_div2 = clk_core_div2_r;

  // ---- /3 : 50%-ish duty odd divider --------------------------------------
  (* keep *) reg [1:0] cnt_dsp_r;
  (* keep *) reg       clk_dsp_div3_r;
  always @(posedge clk_dsp or negedge rst_dsp_n) begin
    if (!rst_dsp_n) begin
      cnt_dsp_r      <= 2'd0;
      clk_dsp_div3_r <= 1'b0;
    end else if (cnt_dsp_r == 2'd2) begin
      cnt_dsp_r      <= 2'd0;
      clk_dsp_div3_r <= ~clk_dsp_div3_r;
    end else begin
      cnt_dsp_r      <= cnt_dsp_r + 2'd1;
    end
  end
  assign clk_dsp_div3 = clk_dsp_div3_r;

  // ---- /4 -----------------------------------------------------------------
  (* keep *) reg [1:0] cnt_mem_r;
  always @(posedge clk_mem or negedge rst_mem_n) begin
    if (!rst_mem_n) cnt_mem_r <= 2'd0;
    else            cnt_mem_r <= cnt_mem_r + 2'd1;
  end
  assign clk_mem_div4 = cnt_mem_r[1];

  // ---- /5 -----------------------------------------------------------------
  (* keep *) reg [2:0] cnt_io_r;
  (* keep *) reg       clk_io_div5_r;
  always @(posedge clk_io or negedge rst_io_n) begin
    if (!rst_io_n) begin
      cnt_io_r      <= 3'd0;
      clk_io_div5_r <= 1'b0;
    end else if (cnt_io_r == 3'd4) begin
      cnt_io_r      <= 3'd0;
      clk_io_div5_r <= ~clk_io_div5_r;
    end else begin
      cnt_io_r      <= cnt_io_r + 3'd1;
    end
  end
  assign clk_io_div5 = clk_io_div5_r;

  // ---- /8 -----------------------------------------------------------------
  (* keep *) reg [2:0] cnt_sec_r;
  always @(posedge clk_sec or negedge rst_sec_n) begin
    if (!rst_sec_n) cnt_sec_r <= 3'd0;
    else            cnt_sec_r <= cnt_sec_r + 3'd1;
  end
  assign clk_sec_div8 = cnt_sec_r[2];

endmodule

`default_nettype wire
