# 05 — The verification gate

Every candidate patch runs this sequence. The first failure stops it, reverts
the patch and logs why.

| # | Gate | Tool | Rejects when |
|---|---|---|---|
| 1 | CDC protection | `genrtl/protect.py` | the patch touches a protected file |
| 2 | Lint / elaboration | Yosys | the patched RTL does not parse or elaborate |
| 3 | Synthesis | Yosys + Liberty | the whole design no longer maps to gates |
| 4 | Formal equivalence | Yosys formal engine | function changed |
| 5 | Timing re-analysis | OpenSTA | setup slack did not improve |
| 6 | Hold safety | OpenSTA | hold slack fell below the floor, or more hold violations |
| 7 | Area budget | Yosys `stat` | cell area grew more than the budget |

Gates 5–7 are the acceptance rule in `genrtl/accept.py`; the rest are in
`genrtl/verify.py`.

## The acceptance rule

A patch is accepted only when **all** of these hold:

```
   WNS on the targeted clock improves by at least min_wns_gain_ns  (default 5 ps)
OR global WNS improves by at least min_wns_gain_ns
OR global WNS is unchanged and TNS improves        # progress on secondary paths
AND global WNS does not regress
AND global TNS does not regress
AND worst hold slack stays at or above hold_floor_ns
AND the hold-violation count does not increase
AND cell area grows by no more than max_area_growth (default 10 %)
```

The TNS clause matters more than it looks. A design frequently has a dozen paths
within picoseconds of each other; fixing one moves WNS by nothing while removing
a large slice of total violation. Accepting on TNS keeps that progress instead of
throwing it away. Iteration 4 of the small run is exactly this case:

```
ACCEPT -- WNS unchanged, accepted on TNS improvement of +7.0464 ns;
          WNS +0.0000 ns, TNS +7.0464 ns, hold +0.0055 ns, area -0.04%
```

The hold gate is not decorative either. Baseline worst hold slack on this
benchmark is +5.5 ps, so a careless launch-path change really can break it.

## The formal engines

Two engines, chosen by the transformation's declared obligation.

### Temporal induction — complete

Used for every transformation that preserves latency and state encoding.

```tcl
prep -top <module> -flatten     # for gold and for gate
memory_map                      # $mem_v2 cells cannot enter the SAT database
async2sync                      # nor can $adff / $adffe
setattr -mod -unset keep_hierarchy   # otherwise -flatten leaves cells behind

miter -equiv -flatten gold gate miter
sat -tempinduct -prove trigger 0 -set-init-zero -verify miter
```

`miter -equiv` builds one miter whose `trigger` output is high iff the two
modules disagree on any output. `-tempinduct` proves the trigger can never rise:
the base case starts from the reset state, and the induction step assumes
agreement for k cycles and proves it for k+1. This is an unbounded proof, not a
bounded one — for a combinational restructuring it closes at k=1, for register
duplication at k=4.

Four practical requirements had to be discovered the hard way, and all four are
now in the flow:

- **`async2sync`** — without it, every `$adffe` aborts the proof with
  "Failed to import cell … to SAT database".
- **`memory_map`** — a `case` statement large enough to be inferred as a ROM
  becomes a `$mem_v2` cell that the SAT database also rejects.
- **`setattr -mod -unset keep_hierarchy`** — the CDC modules carry
  `keep_hierarchy`, which blocks `prep -flatten` and leaves hierarchical cells
  the SAT engine cannot import.
- **`-set-init-zero`** — without an initial-state constraint the base case fails
  immediately with the two designs starting from different register values.

### Bounded model checking with a latency offset

Used for `pipeline_insert`, and as the fallback for transforms that change the
state encoding. `genrtl/verify.py` generates a miter from the module's port
list:

```systemverilog
module genrtl_lat_miter (input wire clk, input wire [127:0] state_in, ...);
  reg [7:0] genrtl_cyc;
  initial genrtl_cyc = 8'd0;
  always @(posedge clk) if (genrtl_cyc != 8'hFF) genrtl_cyc <= genrtl_cyc + 8'd1;
  wire rst_n = (genrtl_cyc >= 8'd2);          // reset sequenced, not assumed

  gold u_gold (.clk(clk), .rst_n(rst_n), ..., .out_valid(g_out_valid), ...);
  gate u_gate (.clk(clk), .rst_n(rst_n), ..., .out_valid(t_out_valid), ...);

  reg [127:0] g_state_q_d1;                   // golden output delayed by K
  reg         g_out_valid_d1;
  always @(posedge clk) begin
    g_state_q_d1    <= g_state_q;
    g_out_valid_d1  <= g_out_valid;
  end

  wire genrtl_settled = (genrtl_cyc >= 8'd4);
  always @(posedge clk) if (genrtl_settled) begin
    assert (t_out_valid == g_out_valid_d1);
    if (t_out_valid) assert (t_state_q == g_state_q_d1);
  end
endmodule
```

then proves it with `sat -seq D -prove-asserts -set-assumes -set-init-zero
-verify`.

The depth `D` is chosen from the flattened cell count against a fixed
cell×cycle budget, because a 12 K-cell AES round and a 2 K-cell MAC cannot
afford the same unrolling. `dsp_mac` proves at depth 24; `aes_round` at depth 8.
The depth actually used is recorded in every proof result and in the report —
a bounded proof is reported as a bounded proof.

### Stability contracts

Pipelining a module whose inputs are read by more than one stage is only
equivalent if those inputs are stable while a transaction is in flight. This is
not a footnote: the AES round consumes `rkey0` in round 0 and `rkey1` in round 1,
and once those rounds sit in different cycles, the keys must not change between
them.

The RTL declares the requirement:

```systemverilog
// genrtl-latency-stable: rkey0, rkey1
```

and the miter turns it into an explicit assumption — the inputs are captured
once and then constrained equal to the captured value:

```systemverilog
always @(posedge clk) if (genrtl_frz_valid) begin
  assume (rkey0 == genrtl_frz_rkey0);
  assume (rkey1 == genrtl_frz_rkey1);
end
```

The proof result records it:

```
bounded equivalence proven to depth 8 with a 1-cycle latency offset,
qualified by out_valid, assuming rkey0, rkey1 stable while a transaction is
in flight (declared by genrtl-latency-stable)
```

Without the annotation the pipelining proof **fails**, and it should. This was
not designed in from the start — the formal gate found it. A 400-cycle
directed simulation with a constant key passed the same patch cleanly.

## Proving at the right parameterisation

Modules are parameterised, and a proof at the module's default parameters says
nothing about the parameterisation the design actually instantiates. Yosys
encodes instantiated parameters in the specialised module name
(`$paramod\dsp_mac\W=s32'0…10000`), so `verify.parameterizations()` recovers
them from `elab.json` and the proof runs once per distinct parameterisation,
with `chparam -set` applied first. Every parameterisation must pass.

## The end-to-end proof

Iteration-by-iteration equivalence is against the *previous accepted* RTL. That
chains by transitivity, but only if nothing in the chain is subtly wrong — so at
the end of the run every changed module is proved again directly against the
untouched original in `runs/<name>/golden/`, with the cumulative latency offset
the run introduced:

```
=== final equivalence vs the original RTL (3 changed file(s)) ===
    dsp_mux_chain          PROVEN (latency +0)
    dsp_mac                PROVEN (latency +1)
    rv_alu                 PROVEN (latency +0)
```

## Negative controls

A verification gate that never fails is worthless. `tests/test_formal.py`
mutates each patch in a way that is subtly wrong — swapping `a7` for `a6` in a
rebalanced adder tree, XORing a constant into a pipeline register — and asserts
that the proof **rejects** it. Both the correct and the mutated version of each
transformation are checked, so the suite fails if the engine ever starts passing
everything.

The suite also cross-checks the AES pipelining transform by simulation
(`tests/sim/`): the golden and pipelined modules run side by side under Icarus
Verilog with random stimulus, compared with a one-cycle offset.
