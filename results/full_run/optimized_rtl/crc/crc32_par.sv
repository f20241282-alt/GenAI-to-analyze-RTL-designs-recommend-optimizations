// ---------------------------------------------------------------------------
// crc32_par -- 32-bit-per-cycle parallel CRC-32 (IEEE 802.3 polynomial)
//               followed by an 8-term whitening fold
//
// OPTIMISATION TARGET (T1 logic_restructure)
//   The whitening fold is written as a left-associative chain of eight '^'
//   terms, so it adds seven levels of XOR on top of an already deep CRC cone.
//   XOR is associative, so the chain can be rebalanced into a depth-3 tree
//   with a complete SAT proof and no change of function.
// ---------------------------------------------------------------------------
`default_nettype none

module crc32_par (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clear,
    input  wire        valid,
    input  wire [31:0] data,
    output reg  [31:0] crc_q
);

  function automatic [31:0] crc_step;
    input [31:0] c;
    input [31:0] d;
    integer b;
    reg [31:0] acc;
    begin
      acc = c ^ d;
      for (b = 0; b < 32; b = b + 1) begin
        acc = acc[31] ? ((acc << 1) ^ 32'h04C11DB7) : (acc << 1);
      end
      crc_step = acc;
    end
  endfunction

  wire [31:0] crc_c = crc_step(crc_q, data);

  // Eight rotations of the raw CRC residue, folded together.
  wire [31:0] f0 = crc_c;
  wire [31:0] f1 = {crc_c[27:0],  crc_c[31:28]};
  wire [31:0] f2 = {crc_c[23:0],  crc_c[31:24]};
  wire [31:0] f3 = {crc_c[19:0],  crc_c[31:20]};
  wire [31:0] f4 = {crc_c[15:0],  crc_c[31:16]};
  wire [31:0] f5 = {crc_c[11:0],  crc_c[31:12]};
  wire [31:0] f6 = {crc_c[7:0],   crc_c[31:8]};
  wire [31:0] f7 = {crc_c[3:0],   crc_c[31:4]};

  // genrtl-target: reduction_chain op=^ terms=8
  wire [31:0] white_c = (((f0 ^ f1) ^ (f2 ^ f3)) ^ ((f4 ^ f5) ^ (f6 ^ f7)));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)      crc_q <= 32'hFFFF_FFFF;
    else if (clear)  crc_q <= 32'hFFFF_FFFF;
    else if (valid)  crc_q <= white_c;
  end

endmodule

`default_nettype wire
