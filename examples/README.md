# examples/ — standalone RTL for the advisor

These files are **not** part of the Nebula benchmark. They exist so you can see
the optimisation advisor working on ordinary, self-contained RTL:

```bash
python -m genrtl suggest examples/
python -m genrtl suggest examples/opcode_alu.sv --format md --out review.md
```

**Bigger, multi-module designs** (good for a full demo):

| file | what the advisor is expected to find |
|---|---|
| `complex_accel.sv` | 2-clock accelerator — mux tree + adder tree + MAC pipelining + dense FSM + boolean factor (**auto**), plus a **multi-bit CDC hazard** and a blocking-`=`-in-a-clocked-block bug |
| `crypto_core.sv` | keyed hash round — long XOR chain, strength reduction, comparator decode, un-pipelined multiplier (**auto** pipelining), common sub-expression |
| `bus_arbiter.sv` | 4-master arbiter — dense FSM (**auto**), resource sharing (2 multipliers), CSE, latch inference, non-blocking-in-combinational bug, incomplete sensitivity |

**Smaller, focused examples:**

| file | what the advisor is expected to find |
|---|---|
| `fir4_unpipelined.sv` | balanced adder tree (**auto**), un-pipelined multiplier, `*2` strength reductions |
| `opcode_alu.sv` | balanced mux tree (**auto**), OR-of-equalities comparator chain, `*4`/`/2` strength reduction, and a blocking-`=`-in-a-clocked-block bug (high severity) |
| `vending_fsm.sv` | one-hot FSM re-encode (**auto**), Boolean factoring (**auto**), and a combinational `case` with no `default` (latch risk) |

`[AUTO]` findings are rewrites the verified engine can apply and formally prove
equivalent; the rest are advisory recommendations for a designer or an LLM.

To try it on your own design, just point `suggest` at any `.sv`/`.v` file or a
directory of them.
