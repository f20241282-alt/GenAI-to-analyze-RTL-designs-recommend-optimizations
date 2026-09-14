// ---------------------------------------------------------------------------
// tb_latency_equiv -- simulation cross-check for a latency-changing transform.
//
// Runs the original module and the pipelined module side by side with random
// stimulus and compares the pipelined output against the original delayed by
// LATENCY cycles, qualified by the pipelined module's own valid output.
//
// This exists to cross-check the formal gate, not to replace it. The AES
// pipelining patch passes 400 randomised cycles here with the round keys held
// constant -- and the formal gate still (correctly) rejects it unless the RTL
// declares that the keys are stable while a block is in flight. Simulation
// agreeing is necessary, not sufficient.
//
// Driven by tests/test_sim.py, which supplies GOLD/GATE/LATENCY via +define+.
// ---------------------------------------------------------------------------
`timescale 1ns/1ps

module tb_latency_equiv;

  localparam integer LAT = `LATENCY;

  reg          clk = 1'b0;
  reg          rst_n = 1'b0;
  reg          in_valid = 1'b0;
  reg  [127:0] state_in = 128'd0;
  reg  [127:0] rkey0 = 128'h0f1571c947d9e8590cb7add6af7f6798;
  reg  [127:0] rkey1 = ~128'h0f1571c947d9e8590cb7add6af7f6798;

  wire         g_valid, t_valid;
  wire [127:0] g_data,  t_data;

  always #5 clk = ~clk;

  `GOLD u_gold (.clk(clk), .rst_n(rst_n), .in_valid(in_valid),
                .state_in(state_in), .rkey0(rkey0), .rkey1(rkey1),
                .out_valid(g_valid), .state_q(g_data));

  `GATE u_gate (.clk(clk), .rst_n(rst_n), .in_valid(in_valid),
                .state_in(state_in), .rkey0(rkey0), .rkey1(rkey1),
                .out_valid(t_valid), .state_q(t_data));

  // golden output delay line
  reg [127:0] g_data_d [0:LAT];
  reg         g_valid_d [0:LAT];
  integer i;
  always @(posedge clk) begin
    g_data_d[0]  <= g_data;
    g_valid_d[0] <= g_valid;
    for (i = 1; i <= LAT; i = i + 1) begin
      g_data_d[i]  <= g_data_d[i-1];
      g_valid_d[i] <= g_valid_d[i-1];
    end
  end

  integer errors = 0, checks = 0, cyc = 0;

  always @(posedge clk) begin
    cyc <= cyc + 1;
    if (rst_n && cyc > 5 + LAT) begin
      if (t_valid !== g_valid_d[LAT-1]) begin
        errors = errors + 1;
        $display("cyc %0d VALID mismatch: gate=%b gold_delayed=%b",
                 cyc, t_valid, g_valid_d[LAT-1]);
      end else if (t_valid) begin
        checks = checks + 1;
        if (t_data !== g_data_d[LAT-1]) begin
          errors = errors + 1;
          $display("cyc %0d DATA mismatch\n  gate=%032x\n  gold=%032x",
                   cyc, t_data, g_data_d[LAT-1]);
        end
      end
    end
  end

  initial begin
    repeat (4) @(posedge clk);
    rst_n <= 1'b1;
    repeat (400) begin
      @(posedge clk);
      state_in <= {$random, $random, $random, $random};
      in_valid <= ($random % 4) != 0;
    end
    repeat (8) @(posedge clk);
    $display("checks=%0d errors=%0d", checks, errors);
    if (errors == 0 && checks > 50) $display("SIM PASS");
    else                            $display("SIM FAIL");
    $finish;
  end

endmodule
