# GenAI RTL timing-closure run report

- benchmark: **full** on **nangate45**
- proposal engine: **heuristic**
- constraints: `/home/claude/nebula-genrtl/constraints/nebula.sdc`
- iterations: 10, runtime 983 s

## Headline metrics

| metric | before | after | delta |
|---|---|---|---|
| WNS (ns) | -7.7260 | -0.8756 | +6.8504 |
| TNS (ns) | -1316.740 | -104.663 | +1212.077 (+92.1%) |
| Worst hold slack (ns) | 0.0055 | 0.0055 | +0.0000 |
| Setup violations | 122 | 101 | -21 |
| Hold violations | 0 | 0 | +0 |
| Cells | 50091 | 50197 | +106 |
| Cell area | 82716 | 83497 | +0.94% |

## Per clock domain

| clock | WNS before | WNS after | TNS before | TNS after | viol before | viol after | Fmax before (MHz) | Fmax after (MHz) |
|---|---|---|---|---|---|---|---|---|
| clk_core | -2.0303 | -0.8296 | -22.729 | -2.489 | 24 | 3 | 104.9 | 120.1 |
| clk_core_div2 | 5.9221 | 5.9221 | 0.000 | 0.000 | 0 | 0 | 66.7 | 66.7 |
| clk_dsp | -1.5884 | -0.8756 | -38.122 | -12.417 | 24 | 24 | 131.8 | 145.4 |
| clk_dsp_div3 | 4.4299 | 4.4299 | 0.000 | 0.000 | 0 | 0 | 27.8 | 27.8 |
| clk_io | -1.0930 | -0.5850 | -26.078 | -12.458 | 24 | 24 | 188.9 | 209.0 |
| clk_io_div5 | 2.5620 | 2.5620 | 0.000 | 0.000 | 0 | 0 | 23.8 | 23.8 |
| clk_mem | -0.6606 | -0.6606 | -15.855 | -15.855 | 24 | 24 | 473.8 | 473.8 |
| clk_mem_div4 | -0.1106 | -0.1106 | -0.196 | -0.196 | 2 | 2 | 169.2 | 169.2 |
| clk_sec | -7.7260 | -0.3450 | -182.870 | -6.391 | 24 | 24 | 78.6 | 187.1 |
| clk_sec_div8 | 3.4219 | 3.4219 | 0.000 | 0.000 | 0 | 0 | 25.0 | 25.0 |

## Optimisation statistics

| statistic | value |
|---|---|
| Patches proposed | 8 |
| Patches accepted | 5 |
| Acceptance rate | 62.5% |
| Formal proofs passed | 7 |
| Formal proofs failed | 0 |
| Critical-path clocks closed | none |
| End-to-end equivalence vs original RTL | PROVEN |

### By transformation

| transform | proposed | accepted |
|---|---|---|
| balanced_mux_tree | 2 | 1 |
| logic_restructure | 1 | 1 |
| pipeline_insert | 2 | 2 |
| register_retime | 3 | 1 |

### Rejections by gate

| gate that rejected | count |
|---|---|
| acceptance | 2 |
| apply | 1 |
| no_actionable_path | 2 |

## Protected (do-not-touch) modules

- **cdc_async_fifo** — path: file lives under rtl/cdc; name: matches /^cdc_/; annotation: genrtl-protect marker in file header
- **cdc_handshake** — path: file lives under rtl/cdc; name: matches /^cdc_/; annotation: genrtl-protect marker in file header
- **cdc_pulse_sync** — path: file lives under rtl/cdc; name: matches /^cdc_/; annotation: genrtl-protect marker in file header
- **cdc_sync_2ff** — path: file lives under rtl/cdc; name: matches /^cdc_/; annotation: genrtl-protect marker in file header; structural: 3-flop synchroniser chain fed from a module input; structural: 2-flop synchroniser chain fed from a module input
- **nebula_clkgen** — path: file lives under rtl/clk; name: matches /clkgen/; annotation: genrtl-protect marker in file header

## Iteration log

### Iteration 1 — pipeline_insert on `aes_round` — **ACCEPTED**

- path: `_2267_/Q` → `g_aes[0].u_round/_2804_/D`
- clock `clk_sec`, slack -7.7260 ns, depth 58
- diagnosis: logic_depth
- rationale: [heuristic] 'chain' is an unrolled generate pipeline of ROUNDS stages evaluated in a single cycle before 'state_q'. Registering the midpoint halves the combinational depth; the module declares a latency tolerance of 1 cycle(s). Diagnosis 'logic_depth' favours pipeline_insert.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=pass
- proof (bmc-latency): bounded equivalence proven to depth 8 with a 1-cycle latency offset, qualified by out_valid, assuming rkey0, rkey1 stable while a transaction is in flight (declared by genrtl-latency-stable)
- verdict: WNS +5.6957 ns, TNS +934.7036 ns, hold +0.0055 ns, area +1.12%

<details><summary>patch</summary>

```diff
--- a/crypto/aes_round.sv
+++ b/crypto/aes_round.sv
@@ -47,6 +47,29 @@
   assign rk[1] = rkey1;
 
   wire [127:0] chain [0:ROUNDS];
+  // >>> genrtl-pipeline-stage 1: inserted by the GenAI optimiser
+  localparam integer GENRTL_PIPE_AT_P1 = (ROUNDS)/2;
+  reg [127:0] genrtl_pipe_p1;
+  reg genrtl_valid_p1;
+  always @(posedge clk or negedge rst_n) begin
+    if (!rst_n) begin
+      genrtl_pipe_p1 <= '0;
+      genrtl_valid_p1 <= 1'b0;
+    end else begin
+      genrtl_valid_p1 <= in_valid;
+      if (in_valid) genrtl_pipe_p1 <= chain[GENRTL_PIPE_AT_P1];
+    end
+  end
+  wire [127:0] chain_genrtl_s1 [0:ROUNDS];
+  genvar genrtl_gp1;
+  generate
+    for (genrtl_gp1 = 0; genrtl_gp1 <= ROUNDS; genrtl_gp1 = genrtl_gp1 + 1) begin : g_chain_genrtl_s1
+      if (genrtl_gp1 == GENRTL_PIPE_AT_P1) assign chain_genrtl_s1[genrtl_gp1] = genrtl_pipe_p1;
+      else              assign chain_genrtl_s1[genrtl_gp1] = chain[genrtl_gp1];
+    end
+  endgenerate
+  // <<< genrtl-pipeline-stage 1
+
   assign chain[0] = state_in;
 
   genvar gr, gb, gc;
@@ -56,7 +79,7 @@
 
       // ---- SubBytes ------------------------------------------------------
       for (gb = 0; gb < 16; gb = gb + 1) begin : g_sub
-        aes_sbox u_sb (.din(chain[gr][gb*8 +: 8]), .dout(sub[gb*8 +: 8]));
+        aes_sbox u_sb (.din(chain_genrtl_s1[gr][gb*8 +: 8]), .dout(sub[gb*8 +: 8]));
       end
 
       // ---- ShiftRows (column-major 4x4 byte state) ------------------------
@@ -89,8 +112,8 @@
       state_q   <= 128'd0;
       out_valid <= 1'b0;
     end else begin
-      out_valid <= in_valid;
-      if (in_valid) state_q <= chain[ROUNDS];
+      out_valid <= genrtl_valid_p1;
+      if (genrtl_valid_p1) state_q <= chain_genrtl_s1[ROUNDS];
     end
   end
```

</details>

### Iteration 2 — balanced_mux_tree on `rv_alu` — **ACCEPTED**

- path: `g_rv[0].u_lane/_105_/Q` → `g_rv[0].u_lane/u_alu/_3629_/D`
- clock `clk_core`, slack -2.0303 ns, depth 68
- diagnosis: single_dominant_stage
- rationale: [heuristic] 'alu_c' selects among 16 values with a 16-deep priority chain on 'op'. Every code of 'op' is covered, so the chain is equivalent to a depth-4 balanced tree over its bits. Diagnosis 'single_dominant_stage' favours balanced_mux_tree.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=pass
- proof (yosys-tempinduct): equivalence proven by temporal induction (k=1)
- verdict: WNS +0.4419 ns, TNS +51.6492 ns, hold +0.0055 ns, area -0.77%

<details><summary>patch</summary>

```diff
--- a/core/rv_alu.sv
+++ b/core/rv_alu.sv
@@ -40,21 +40,7 @@
   wire [31:0] alu_c;
 
   // genrtl-target: ternary_chain sel=op width=16
-  assign alu_c = (op == OP_ADD)  ? add_r :
-                 (op == OP_SUB)  ? sub_r :
-                 (op == OP_SLL)  ? sll_r :
-                 (op == OP_SLT)  ? {31'd0, lts} :
-                 (op == OP_SLTU) ? {31'd0, ltu} :
-                 (op == OP_XOR)  ? (rs1 ^ rs2) :
-                 (op == OP_SRL)  ? srl_r :
-                 (op == OP_SRA)  ? sra_r :
-                 (op == OP_OR)   ? (rs1 | rs2) :
-                 (op == OP_AND)  ? (rs1 & rs2) :
-                 (op == OP_MINU) ? (ltu ? rs1 : rs2) :
-                 (op == OP_MAXU) ? (ltu ? rs2 : rs1) :
-                 (op == OP_ROL)  ? rol_r :
-                 (op == OP_ROR)  ? ror_r :
-                 (op == OP_ANDN) ? (rs1 & ~rs2) : (rs1 | ~rs2);
+  assign alu_c = (op[3] ? (op[2] ? (op[1] ? (op[0] ? ((rs1 | ~rs2)) : ((rs1 & ~rs2))) : (op[0] ? (ror_r) : (rol_r))) : (op[1] ? (op[0] ? ((ltu ? rs2 : rs1)) : ((ltu ? rs1 : rs2))) : (op[0] ? ((rs1 & rs2)) : ((rs1 | rs2))))) : (op[2] ? (op[1] ? (op[0] ? (sra_r) : (srl_r)) : (op[0] ? ((rs1 ^ rs2)) : ({31'd0, ltu}))) : (op[1] ? (op[0] ? ({31'd0, lts}) : (sll_r)) : (op[0] ? (sub_r) : (add_r)))));
 
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n) begin
```

</details>

### Iteration 3 — pipeline_insert on `dsp_mac` — **ACCEPTED**

- path: `g_dsp[0].u_lane/u_mux/_1298_/Q` → `g_dsp[0].u_lane/u_mac/_3357_/D`
- clock `clk_dsp`, slack -1.5884 ns, depth 78
- diagnosis: logic_depth
- rationale: [heuristic] The combinational chain prod_c -> sum_c -> sat_c runs between the input registers and the output register. Cutting after 'prod_c' splits it into 1 and 2 levels; the module declares a latency tolerance of 2 cycle(s). Diagnosis 'logic_depth' favours pipeline_insert.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=pass
- proof (bmc-latency): bounded equivalence proven to depth 24 with a 1-cycle latency offset, qualified by out_valid
- verdict: WNS +0.4954 ns, TNS +124.0388 ns, hold +0.0055 ns, area +2.29%

<details><summary>patch</summary>

```diff
--- a/dsp/dsp_mac.sv
+++ b/dsp/dsp_mac.sv
@@ -29,7 +29,26 @@
 );
 
   wire [2*W-1:0] prod_c = a * b;
-  wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c} + c;
+
+  // >>> genrtl-pipeline-stage 1: inserted by the GenAI optimiser
+  reg [2*W-1:0] prod_c_p1;
+  reg [ACC-1:0] c_p1;
+  reg genrtl_valid_p1;
+  always @(posedge clk or negedge rst_n) begin
+    if (!rst_n) begin
+      prod_c_p1 <= '0;
+      c_p1 <= '0;
+      genrtl_valid_p1 <= 1'b0;
+    end else begin
+      genrtl_valid_p1 <= in_valid;
+      if (in_valid) begin
+        prod_c_p1 <= prod_c;
+        c_p1 <= c;
+      end
+    end
+  end
+  // <<< genrtl-pipeline-stage 1
+  wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c_p1} + c_p1;
   wire [ACC-1:0] sat_c  = sum_c[ACC-1] ? {ACC{1'b1}} : sum_c;
 
   always @(posedge clk or negedge rst_n) begin
@@ -37,8 +56,8 @@
       acc_q     <= {ACC{1'b0}};
       out_valid <= 1'b0;
     end else begin
-      out_valid <= in_valid;
-      if (in_valid) acc_q <= sat_c;
+      out_valid <= genrtl_valid_p1;
+      if (genrtl_valid_p1) acc_q <= sat_c;
     end
   end
```

</details>

### Iteration 4 — logic_restructure on `crc32_par` — **ACCEPTED**

- path: `g_crc[0].u_crc/_859_/Q` → `g_crc[0].u_crc/_829_/D`
- clock `clk_io`, slack -1.0930 ns, depth 40
- diagnosis: logic_depth
- rationale: [heuristic] 'white_c' is a left-associative chain of 8 '^' terms (logic depth 7). '^' is associative, so the chain can be rebalanced into a depth-3 tree with an identical combinational function. Diagnosis 'logic_depth' favours logic_restructure.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=pass
- proof (yosys-tempinduct): equivalence proven by temporal induction (k=1)
- verdict: WNS +0.1009 ns, TNS +60.6536 ns, hold +0.0055 ns, area -0.45%

<details><summary>patch</summary>

```diff
--- a/crc/crc32_par.sv
+++ b/crc/crc32_par.sv
@@ -46,7 +46,7 @@
   wire [31:0] f7 = {crc_c[3:0],   crc_c[31:4]};
 
   // genrtl-target: reduction_chain op=^ terms=8
-  wire [31:0] white_c = f0 ^ f1 ^ f2 ^ f3 ^ f4 ^ f5 ^ f6 ^ f7;
+  wire [31:0] white_c = (((f0 ^ f1) ^ (f2 ^ f3)) ^ ((f4 ^ f5) ^ (f6 ^ f7)));
 
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n)      crc_q <= 32'hFFFF_FFFF;
```

</details>

### Iteration 5 — register_retime on `dsp_mac` — **ACCEPTED**

- path: `g_dsp[0].u_lane/u_mux/_1298_/Q` → `g_dsp[0].u_lane/u_mac/_3706_/D`
- clock `clk_dsp`, slack -0.9921 ns, depth 72
- diagnosis: logic_depth
- rationale: [heuristic] The inserted stage sits after 1 of 3 chain levels. Moving it one level later rebalances the two stages. Diagnosis 'logic_depth' favours register_retime.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=pass
- proof (yosys-tempinduct): equivalence proven by temporal induction (k=2)
- verdict: WNS +0.1166 ns, TNS +41.0317 ns, hold +0.0055 ns, area -1.22%

<details><summary>patch</summary>

```diff
--- a/dsp/dsp_mac.sv
+++ b/dsp/dsp_mac.sv
@@ -30,26 +30,24 @@
 
   wire [2*W-1:0] prod_c = a * b;
 
+    wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c} + c;
+
   // >>> genrtl-pipeline-stage 1: inserted by the GenAI optimiser
-  reg [2*W-1:0] prod_c_p1;
-  reg [ACC-1:0] c_p1;
+  reg [ACC-1:0] sum_c_p1;
   reg genrtl_valid_p1;
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n) begin
-      prod_c_p1 <= '0;
-      c_p1 <= '0;
+      sum_c_p1 <= '0;
       genrtl_valid_p1 <= 1'b0;
     end else begin
       genrtl_valid_p1 <= in_valid;
       if (in_valid) begin
-        prod_c_p1 <= prod_c;
-        c_p1 <= c;
+        sum_c_p1 <= sum_c;
       end
     end
   end
   // <<< genrtl-pipeline-stage 1
-  wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c_p1} + c_p1;
-  wire [ACC-1:0] sat_c  = sum_c[ACC-1] ? {ACC{1'b1}} : sum_c;
+  wire [ACC-1:0] sat_c  = sum_c_p1[ACC-1] ? {ACC{1'b1}} : sum_c_p1;
 
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n) begin
```

</details>

### Iteration 6 — register_retime on `dsp_mac` — **REJECTED**

- path: `g_dsp[0].u_lane/u_mux/_1298_/Q` → `g_dsp[0].u_lane/u_mac/_3615_/D`
- clock `clk_dsp`, slack -0.8756 ns, depth 74
- diagnosis: logic_depth
- rationale: [heuristic] The inserted stage sits after 2 of 3 chain levels. Moving it one level earlier rebalances the two stages. Diagnosis 'logic_depth' favours register_retime.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=FAIL
- proof (yosys-tempinduct): equivalence proven by temporal induction (k=2)
- verdict: no timing improvement (WNS -0.1166 ns, target-domain WNS -0.1166 ns, threshold 0.0050 ns)

<details><summary>patch</summary>

```diff
--- a/dsp/dsp_mac.sv
+++ b/dsp/dsp_mac.sv
@@ -30,24 +30,28 @@
 
   wire [2*W-1:0] prod_c = a * b;
 
-    wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c} + c;
 
   // >>> genrtl-pipeline-stage 1: inserted by the GenAI optimiser
-  reg [ACC-1:0] sum_c_p1;
+  reg [2*W-1:0] prod_c_p1;
+  reg [ACC-1:0] c_p1;
   reg genrtl_valid_p1;
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n) begin
-      sum_c_p1 <= '0;
+      prod_c_p1 <= '0;
+      c_p1 <= '0;
       genrtl_valid_p1 <= 1'b0;
     end else begin
       genrtl_valid_p1 <= in_valid;
       if (in_valid) begin
-        sum_c_p1 <= sum_c;
+        prod_c_p1 <= prod_c;
+        c_p1 <= c;
       end
     end
   end
   // <<< genrtl-pipeline-stage 1
-  wire [ACC-1:0] sat_c  = sum_c_p1[ACC-1] ? {ACC{1'b1}} : sum_c_p1;
+    wire [ACC-1:0] sum_c  = {{(ACC-2*W){1'b0}}, prod_c_p1} + c_p1;
+
+    wire [ACC-1:0] sat_c  = sum_c[ACC-1] ? {ACC{1'b1}} : sum_c;
 
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n) begin
```

</details>

### Iteration 7 — register_retime on `aes_round` — **REJECTED**

- path: `_2259_/Q` → `g_aes[0].u_round/_3381_/D`
- clock `clk_sec`, slack -0.3450 ns, depth 32
- diagnosis: moderate_depth
- rationale: [heuristic] The inserted stage sits after 0 of 4 chain levels. Moving it one level later rebalances the two stages. Diagnosis 'moderate_depth' favours register_retime.
- gates: apply=FAIL
- verdict: patch generation failed: no combinational chain left to cut

### Iteration 8 — balanced_mux_tree on `dsp_mux_chain` — **REJECTED**

- path: `dsp_sel[3]` → `g_dsp[1].u_lane/u_mux/_1298_/D`
- clock `clk_dsp`, slack -0.2988 ns, depth 28
- diagnosis: single_dominant_stage
- rationale: [heuristic] 'mux_c' selects among 16 values with a 16-deep priority chain on 'sel'. Every code of 'sel' is covered, so the chain is equivalent to a depth-4 balanced tree over its bits. Diagnosis 'single_dominant_stage' favours balanced_mux_tree.
- gates: cdc_protection=pass, lint=pass, synthesis=pass, formal_equivalence=pass, sta=pass, acceptance=FAIL
- proof (yosys-tempinduct): equivalence proven by temporal induction (k=1)
- verdict: no timing improvement (WNS -8.9864 ns, target-domain WNS -8.9864 ns, threshold 0.0050 ns)

<details><summary>patch</summary>

```diff
--- a/dsp/dsp_mux_chain.sv
+++ b/dsp/dsp_mux_chain.sv
@@ -30,21 +30,7 @@
   wire [W-1:0] mux_c;
 
   // genrtl-target: ternary_chain sel=sel width=16
-  assign mux_c = (sel == 4'd0)  ? d[0]  :
-                 (sel == 4'd1)  ? d[1]  :
-                 (sel == 4'd2)  ? d[2]  :
-                 (sel == 4'd3)  ? d[3]  :
-                 (sel == 4'd4)  ? d[4]  :
-                 (sel == 4'd5)  ? d[5]  :
-                 (sel == 4'd6)  ? d[6]  :
-                 (sel == 4'd7)  ? d[7]  :
-                 (sel == 4'd8)  ? d[8]  :
-                 (sel == 4'd9)  ? d[9]  :
-                 (sel == 4'd10) ? d[10] :
-                 (sel == 4'd11) ? d[11] :
-                 (sel == 4'd12) ? d[12] :
-                 (sel == 4'd13) ? d[13] :
-                 (sel == 4'd14) ? d[14] : d[15];
+  assign mux_c = (sel[3] ? (sel[2] ? (sel[1] ? (sel[0] ? (d[15]) : (d[14])) : (sel[0] ? (d[13]) : (d[12]))) : (sel[1] ? (sel[0] ? (d[11]) : (d[10])) : (sel[0] ? (d[9]) : (d[8])))) : (sel[2] ? (sel[1] ? (sel[0] ? (d[7]) : (d[6])) : (sel[0] ? (d[5]) : (d[4]))) : (sel[1] ? (sel[0] ? (d[3]) : (d[2])) : (sel[0] ? (d[1]) : (d[0])))));
 
   always @(posedge clk or negedge rst_n) begin
     if (!rst_n)  dout_q <= {W{1'b0}};
```

</details>

### Iteration 9 — no actionable path

every violating path is protected, exhausted, or matches no catalogued transformation

### Iteration 10 — no actionable path

every violating path is protected, exhausted, or matches no catalogued transformation


## Final equivalence against the untouched original RTL

| file | module | engine | latency | result | detail |
|---|---|---|---|---|---|
| dsp/dsp_mac.sv | dsp_mac | bmc-latency | +1 | PROVEN | bounded equivalence proven to depth 24 with a 1-cycle latency offset, qualified by out_val |
| crypto/aes_round.sv | aes_round | bmc-latency | +1 | PROVEN | bounded equivalence proven to depth 8 with a 1-cycle latency offset, qualified by out_vali |
| crc/crc32_par.sv | crc32_par | yosys-tempinduct | +0 | PROVEN | equivalence proven by temporal induction (k=1) |
| core/rv_alu.sv | rv_alu | yosys-tempinduct | +0 | PROVEN | equivalence proven by temporal induction (k=1) |
