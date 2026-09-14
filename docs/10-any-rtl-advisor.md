# 10 — The "any RTL" optimisation advisor

The closed loop (`python -m genrtl optimize`) is the guaranteed path: it measures
a real critical path with OpenSTA, localises it, proposes one catalogued
transform, and **formally proves** the rewrite before accepting it. That needs
Yosys and OpenSTA installed, and it operates on the project's benchmark.

The **advisor** answers the lighter, everyday question the brief asks for —
*"here is some RTL, what should I optimise?"* — for **any** RTL, with **nothing
but the Python standard library**. It is the "GenAI recommends RTL
optimisations" surface, and it runs in well under a second on a laptop with no
EDA tools.

```bash
# any file or directory of .sv/.v
python -m genrtl suggest path/to/your_design.sv
python -m genrtl suggest rtl/ examples/
python -m genrtl suggest my_core/ --format md --out review.md
python -m genrtl suggest my_core/ --min-severity medium --only pipelining,fsm_encoding
python -m genrtl suggest my_core/ --auto-only          # only verifiable rewrites
python -m genrtl suggest my_core/ --narrate --llm anthropic   # add an LLM review
```

## Two kinds of finding

| kind | source | meaning |
|---|---|---|
| **auto** | the same verified library the closed loop uses (`genrtl/transforms.py`) | a rewrite the engine can apply **and formally prove equivalent**. Highest confidence. |
| **advisory** | the pattern detectors in `genrtl/advisor.py` | a recommendation for a human or an LLM to act on. Not auto-applied. |

Every finding carries a **category**, a **severity** (high / medium / low), the
**file and line**, the offending **snippet**, a **rationale**, a concrete
**suggested action**, and the **expected benefit**. `--format json` emits all of
that for tooling; `--format md` emits a review document.

## What it looks for

**Auto-appliable (verified) — the eight catalogued transforms**
`balanced_adder_tree`, `logic_restructure`, `balanced_mux_tree`,
`boolean_factor`, `register_retime`, `pipeline_insert`, `fsm_reencode`,
`resource_duplication`. See [docs/04](04-transform-library.md).

**Advisory — timing / area / performance**

| category | what it flags | suggested action |
|---|---|---|
| `combinational_depth` | long associative `+`/`^`/`&`/`|` chains the verified layer can't take (non-simple terms) | rebalance into a tree |
| `comparator_chain` | `(x==k0) \| (x==k1) \| …` | decode `x` once, or use a range/`inside` test |
| `priority_chain` | deep `(s==k)?…:` selection | flatten to a `case` / balanced select |
| `strength_reduction` | `* / %` by a power of two | shift / mask |
| `pipelining` | a multiplier in a single-cycle cloud | register it (declare a latency contract) |
| `resource_sharing` | the same multiplier/divider in mutually-exclusive branches | mux the operands, share one unit |
| `common_subexpression` | an identical non-trivial expression computed more than once | compute once into a wire |
| `fanout` | a register with very wide load | duplicate and split the loads |

**Advisory — correctness (flag, never auto-rewrite)**

| category | what it flags |
|---|---|
| `blocking_in_sequential` | a blocking `=` inside `always @(posedge …)` (sim/synth mismatch risk) |
| `nonblocking_in_combinational` | a non-blocking `<=` inside `always @(*)` |
| `latch_inference` | a combinational `case` with no `default` |
| `incomplete_sensitivity` | an explicit sensitivity list on combinational logic |
| `cdc_multibit` | a multi-bit signal sampled by an unrelated clock inside one module, with no synchroniser |

The `cdc_multibit` check is the counterpart to the closed loop's CDC
**protection**: the loop refuses to *touch* real synchronisers, while the
advisor points out places that *need* one. It is deliberately conservative — it
never fires on a module whose name marks it a synchroniser, and the benchmark's
real crossings (which go through protected submodules) produce zero false
positives.

## How the line mapping works, safely

The advisor reuses the same comment- and bracket-aware lexical utilities as the
transform engine (`genrtl/rtlparse.py`): comments are blanked without moving
offsets, bit-range brackets (`[2*W-1:0]`) are excluded so a width expression is
never mistaken for a datapath multiply, and operator splitting respects bracket
depth. That is why the reported line and snippet always point at the real code.

## The optional LLM pass

`--narrate` feeds each module's source plus its detected findings to an LLM
(reusing the backends in `genrtl/proposer.py`) and prints a short natural-
language review per module. It is never required: with no API key it degrades to
a deterministic textual summary, so the command never hard-fails. The LLM is
used to *explain and prioritise* — the findings themselves come from the
deterministic detectors, so the model cannot invent an issue that is not in the
code.

## Tests

`tests/test_advisor.py` covers the advisor on the examples and the benchmark
with no toolchain, including that it does **not** false-flag the benchmark's CDC
or the `(* keep *)` attributes. `tests/test_protect_structural.py` checks the
structural synchroniser detector on a hand-written netlist fixture. Both run
under `python tests/run_all.py --fast`.
