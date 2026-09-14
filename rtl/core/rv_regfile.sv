// ---------------------------------------------------------------------------
// rv_regfile -- 32 x 32 two-read one-write register file
// Pure datapath, not protected. Contributes bulk cell count and a wide
// read multiplexer that feeds the ALU critical path.
// ---------------------------------------------------------------------------
`default_nettype none

module rv_regfile (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        we,
    input  wire [4:0]  waddr,
    input  wire [31:0] wdata,
    input  wire [4:0]  raddr0,
    input  wire [4:0]  raddr1,
    output wire [31:0] rdata0,
    output wire [31:0] rdata1
);

  reg [31:0] regs_r [0:31];

  integer i;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < 32; i = i + 1) regs_r[i] <= 32'd0;
    end else if (we && (waddr != 5'd0)) begin
      regs_r[waddr] <= wdata;
    end
  end

  assign rdata0 = (raddr0 == 5'd0) ? 32'd0 : regs_r[raddr0];
  assign rdata1 = (raddr1 == 5'd0) ? 32'd0 : regs_r[raddr1];

endmodule

`default_nettype wire
