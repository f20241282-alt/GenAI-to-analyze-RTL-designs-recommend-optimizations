# 04 — The transformation library

The model does not write RTL. It picks one of eight catalogued transformations
and supplies its parameters; `genrtl/transforms.py` performs the rewrite.

This is the single most important design decision in the project. A language
model asked to emit a unified diff against SystemVerilog will produce something
that *looks* right most of the time, and the failure mode is a design that
synthesises, meets timing, and is functionally wrong in a way nobody notices
until silicon. Constraining the output to `{transform, params}` removes that
whole class of failure: the only thing the model can get wrong is *which*
transformation to try, and a wrong choice costs one rejected iteration.

Each transform declares three things:

- **kind** — `latency_preserving` or `latency_changing`
- **equivalence** — which proof obligation applies (see [05](05-verification-gate.md))
- **precondition** — a structural pattern that must actually be present

`detect()` returns concrete parameterised sites; `apply()` produces the patch.
A transform that finds no site is never offered, which is why the loop reports
"no catalogued transformation matches this path" rather than guessing.

## The eight transformations

### 1. `logic_restructure` — rebalance an associative bitwise chain

Rewrites a left-associative chain of `^`, `|` or `&` into a minimum-depth tree.
Precondition: four or more terms at bracket depth zero, all of them simple
operands. Depth goes from N−1 to ⌈log₂N⌉.

*Proof: temporal induction (complete).*

### 2. `balanced_adder_tree` — rebalance an addition chain

```systemverilog
// before: logic depth 7
assign sum_c = a0 + a1 + a2 + a3 + a4 + a5 + a6 + a7;

// after: logic depth 3
assign sum_c = (((a0 + a1) + (a2 + a3)) + ((a4 + a5) + (a6 + a7)));
```

Integer addition is associative modulo 2^W, and Verilog propagates the context
width to every operand of an arithmetic expression, so re-parenthesising is
bit-exact including carry-out behaviour. The transform still refuses terms that
are not simple operands, because context-width propagation through a conditional
or a comparison is not the same.

*Proof: temporal induction (complete).*

### 3. `balanced_mux_tree` — priority chain to balanced tree

```systemverilog
// before: 16 muxes deep
assign alu_c = (op == OP_ADD)  ? add_r :
               (op == OP_SUB)  ? sub_r :
               ...
               (op == OP_ANDN) ? (rs1 & ~rs2) : (rs1 | ~rs2);

// after: 4 muxes deep
assign alu_c = (op[3] ? (op[2] ? ... ) : (op[2] ? ... ));
```

The precondition is strict and checked: the select must be a plain vector that
can be bit-indexed, every comparison value must be a constant (localparams are
resolved), the values must be distinct, and together with the final `else` they
must cover **every** code of the select. Only then does the chain have no
priority semantics left to preserve. `rv_alu` and `dsp_mux_chain` both qualify;
a chain with a genuine default that can be reached does not.

*Proof: temporal induction (complete).*

### 4. `register_retime` — move an existing pipeline boundary

Deletes a previously inserted pipeline stage, restores the signals it renamed,
and re-inserts it one level earlier or later in the chain. Latency is unchanged,
so this rebalances two adjacent stages rather than adding one. Precondition: a
stage created by `pipeline_insert` already exists.

*Proof: temporal induction, with bounded fallback.*

### 5. `pipeline_insert` — add a pipeline stage (latency changing)

Two chain shapes are recognised.

**Linear wire chain** (`dsp_mac`): a sequence of `wire x = expr;` declarations
feeding a registered output. The transform cuts after a named wire, registers
every signal that crosses the cut — including module *inputs* consumed
downstream — and delays the valid qualifier by one cycle.

```systemverilog
  wire [2*W-1:0] prod_c = a * b;
  // >>> genrtl-pipeline-stage 1: inserted by the GenAI optimiser
  reg [2*W-1:0] prod_c_p1;
  reg [ACC-1:0] c_p1;
  reg genrtl_valid_p1;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin prod_c_p1 <= '0; c_p1 <= '0; genrtl_valid_p1 <= 1'b0; end
    else begin
      genrtl_valid_p1 <= in_valid;
      if (in_valid) begin prod_c_p1 <= prod_c; c_p1 <= c; end
    end
  end
  // <<< genrtl-pipeline-stage 1
  wire [ACC-1:0] sum_c = {{(ACC-2*W){1'b0}}, prod_c_p1} + c_p1;
```

**Unrolled generate pipeline** (`aes_round`): a `wire [W] chain [0:N]` array
driven by a generate loop. The transform registers the midpoint and routes the
rounds through a shadow array so that stages before the cut read the
combinational value and stages after it read the registered one.

Both forms require the module to publish a latency contract:

```systemverilog
// genrtl-latency-flex: 1
```

and to expose a valid output. Without the annotation, the transform is not
offered — a module whose consumer cannot tolerate extra latency must not be
pipelined, and the optimiser has no way to know that from the RTL alone.

*Proof: bounded sequential equivalence with an explicit latency offset,
qualified by the valid output.*

### 6. `fsm_reencode` — dense encoding to one-hot

Finds a `case` on a register whose items are localparams, rewrites the state
constants as one-hot codes and widens the state register and its next-state
companion:

```systemverilog
localparam [3:0]  S_IDLE = 4'd0, S_ARM = 4'd1, ...     // before
localparam [11:0] S_IDLE = 12'b000000000001, ...       // after
reg [11:0] state_r;
```

Every `state_r == S_x` test collapses from a 4-bit comparator to a single bit
read. Cost: 8 extra flops.

This only works when the module's outputs do not expose the encoding. `ctrl_fsm`
decodes `phase_q` from the state through an explicit case for exactly this
reason — and the gate is what enforces it, not the author's care: an earlier
version assigned `phase_q <= state_c` directly and the proof correctly failed.

*Proof: bounded sequential equivalence from reset (induction cannot apply — the
two machines never share a state vector).*

### 7. `resource_duplication` — split a high-fanout register

Duplicates a register that drives a wide load and splits the read sites
round-robin across the copies. The precondition is checked, not assumed:
**exactly one** `always` block may drive the register, and that block must drive
nothing else — otherwise duplicating it would create multiple drivers for the
other signals. The whole driving block is replicated with only the assignment
target renamed, so every copy computes an identical next state by construction.

Only offered when the path diagnosis is `single_dominant_stage` or
`load_or_fanout`. Duplication does nothing for a path that is simply too deep.

*Proof: temporal induction (the copies are exact clones, so induction closes at
small k).*

### 8. `boolean_factor` — factor a sum of products

```systemverilog
// before
wire busy_c = (in_flight & status[4]) | (in_flight & status[5]) |
              (in_flight & status[6]) | (in_flight & done_in);

// after
wire busy_c = in_flight & (status[4] | status[5] | status[6] | done_in);
```

Removes N−1 AND gates and puts the shared term one gate from the output instead
of N.

*Proof: temporal induction (complete).*

## Mapping to the project brief

The brief's proposed library maps one-to-one:

| Brief | Implemented as |
|---|---|
| Logic restructuring | `logic_restructure` |
| Balanced adder trees | `balanced_adder_tree` |
| Balanced MUX trees | `balanced_mux_tree` |
| Register retiming | `register_retime` |
| Limited pipeline insertion | `pipeline_insert` |
| FSM optimisation / re-encoding | `fsm_reencode` |
| Resource duplication for high fanout | `resource_duplication` |
| Boolean simplification | `boolean_factor` |

## Inspecting the catalogue

```bash
python3 -m genrtl transforms            # the catalogue with parameter schemas
python3 -m genrtl transforms --scan     # every site found in the current RTL
```

## Adding a transformation

Subclass `Transform`, implement `detect()` and `apply()`, declare `kind` and
`equivalence`, and register it. `detect()` must be conservative: it is cheaper
to miss an opportunity than to generate a patch that burns an iteration. The
verification gate is a backstop, not a substitute for a real precondition.
