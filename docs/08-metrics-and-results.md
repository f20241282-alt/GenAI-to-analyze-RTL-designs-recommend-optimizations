# 08 — Metrics and results

Every number here comes from Yosys `stat` and OpenSTA `report_checks` /
`worst_slack` / `total_negative_slack` on the machine that ran the flow. Nothing
is estimated, and the run that produced them is reproducible with no API key:

```bash
python3 -m genrtl --bench full optimize --name full_run --iters 16 --paths 24
```

Platform: Nangate45, slow corner for setup, fast corner for hold. Two cores,
8 GB RAM.

## Headline — full benchmark (~50 K cells)

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| **WNS** | −7.7260 ns | **−0.8756 ns** | **+6.8504 ns (−89 %)** |
| **TNS** | −1316.74 ns | **−104.66 ns** | **+1212.08 ns (−92 %)** |
| Worst hold slack | +0.0055 ns | +0.0055 ns | unchanged |
| Setup violations | 122 | 101 | −21 |
| Hold violations | 0 | 0 | 0 |
| Cells | 50 091 | 50 197 | +106 |
| **Cell area** | 82 716 | 83 497 | **+0.94 %** |
| End-to-end runtime | — | — | 983 s (16 min) |

Area overhead is **0.94 %**, against a target of "below approximately 10 %".

## Per clock domain

| Clock | WNS before | WNS after | Fmax before | Fmax after |
|---|---:|---:|---:|---:|
| `clk_sec` | −7.7260 | −0.3450 | 78.6 MHz | **187.1 MHz** |
| `clk_core` | −2.0303 | −0.8296 | 104.9 MHz | 120.1 MHz |
| `clk_dsp` | −1.5884 | −0.8756 | 131.8 MHz | 145.4 MHz |
| `clk_io` | −1.0930 | −0.5850 | 188.9 MHz | 209.0 MHz |
| `clk_mem` | −0.6606 | −0.6606 | 473.8 MHz | 473.8 MHz |
| `clk_mem_div4` | −0.1106 | −0.1106 | 169.2 MHz | 169.2 MHz |
| `clk_core_div2` | +5.9221 | +5.9221 | 66.7 MHz | 66.7 MHz |
| `clk_dsp_div3` | +4.4299 | +4.4299 | 27.8 MHz | 27.8 MHz |
| `clk_io_div5` | +2.5620 | +2.5620 | 23.8 MHz | 23.8 MHz |
| `clk_sec_div8` | +3.4219 | +3.4219 | 25.0 MHz | 25.0 MHz |

The AES domain gains 2.4× in achievable frequency from a single pipeline stage.
`clk_mem` and `clk_mem_div4` are untouched: their remaining violating paths sit
inside protected CDC logic or match no catalogued transformation, and the
optimiser says so rather than forcing something through.

## Optimisation statistics

| | |
|---|---:|
| Iterations run | 10 (of 16 allowed) |
| Patches proposed | 8 |
| Patches accepted | 5 |
| **Acceptance rate** | **62.5 %** |
| Formal proofs run | 7 |
| Formal proofs passed | 7 |
| Formal proofs failed | 0 |
| End-to-end equivalence vs original RTL | **PROVEN, all 4 changed modules** |

Rejections by the gate that stopped them: `acceptance` 2, `apply` 1,
`no_actionable_path` 2. The loop stopped on its own when two consecutive
iterations found nothing left in the catalogue that applied.

### By transformation

| Transform | Proposed | Accepted |
|---|---:|---:|
| `pipeline_insert` | 2 | 2 |
| `register_retime` | 3 | 1 |
| `balanced_mux_tree` | 2 | 1 |
| `logic_restructure` | 1 | 1 |

## The iteration log, in full

```
[01] clk_sec  −7.7260  pipeline_insert   on aes_round
     bmc-latency, depth 8, +1 cycle, assuming rkey0/rkey1 stable
     ACCEPT   WNS +5.6957  TNS +934.70  hold +0.0055  area +1.12 %

[02] clk_core −2.0303  balanced_mux_tree on rv_alu
     temporal induction, k=1
     ACCEPT   WNS +0.4419  TNS  +51.65  hold +0.0055  area −0.77 %

[03] clk_dsp  −1.5884  pipeline_insert   on dsp_mac
     bmc-latency, depth 24, +1 cycle
     ACCEPT   WNS +0.4954  TNS +124.04  hold +0.0055  area +2.29 %

[04] clk_io   −1.0930  logic_restructure on crc32_par
     temporal induction, k=1
     ACCEPT   WNS +0.1009  TNS  +60.65  hold +0.0055  area −0.45 %

[05] clk_dsp  −0.9921  register_retime   on dsp_mac
     temporal induction, k=2
     ACCEPT   WNS +0.1166  TNS  +41.03  hold +0.0055  area −1.22 %

[06] clk_dsp  −0.8756  register_retime   on dsp_mac
     temporal induction, k=2
     REJECT   no timing improvement (WNS −0.1166 ns)

[07] clk_sec  −0.3450  register_retime   on aes_round
     REJECT   patch generation failed: no combinational chain left to cut

[08] clk_dsp  −0.2988  balanced_mux_tree on dsp_mux_chain
     temporal induction, k=1
     REJECT   no timing improvement (WNS −8.9864 ns)

[09] no catalogued transformation applies to any remaining violating path
[10] no catalogued transformation applies to any remaining violating path
```

## The three rejections are the interesting part

**Iteration 6 — the loop refusing to loop.** `register_retime` moved the
`dsp_mac` pipeline boundary one level further, which is exactly the inverse of
the move accepted in iteration 5. Formally correct, measurably worse, rejected.
Without a timing gate, an optimiser with this transform in its catalogue would
oscillate forever.

**Iteration 7 — a precondition doing its job.** `register_retime` on
`aes_round` was proposed, but `aes_round` uses the generate-array pipeline form
and has no linear wire chain left to cut. The transform refused to emit a patch
rather than emitting a broken one.

**Iteration 8 — the one that matters.** `balanced_mux_tree` on `dsp_mux_chain`
converts a 16-deep priority chain into a 4-deep balanced tree. It is *proved
functionally identical* by temporal induction. It reduces logic depth by every
static measure. And it made the design **8.99 ns slower**:

```
clk_dsp  before −0.8756   after −9.8620
```

Re-analysing the candidate netlist independently confirms it — this is not a
flow artefact. Written as a priority chain, Yosys builds a `$pmux` that ABC maps
efficiently into the surrounding multiplier cone; written as an explicit binary
tree it becomes a chain of `$mux` cells that ABC maps far worse in this context.
The same transformation on the same module was *accepted* in the small
configuration, where it improved TNS by 7 ns.

That is the entire argument for the architecture in one iteration. A
depth-reducing rewrite, formally proven correct, that a static heuristic would
have applied and a human reviewer would have approved — and it was 9 ns worse.
Only measurement can tell, which is why the tools are the judges and the model
is not.

## Small configuration (~21 K cells)

Same flow, faster iteration. Used for development and for the regression suite.

| Metric | Before | After | Delta |
|---|---:|---:|---:|
| WNS | −2.0303 ns | −0.8490 ns | +1.1814 ns (−58 %) |
| TNS | −100.84 ns | −18.71 ns | +82.13 ns (−81 %) |
| Worst hold slack | +0.0055 ns | +0.0055 ns | unchanged |
| Setup violations | 82 | 66 | −16 |
| Cells | 20 796 | 20 596 | −200 |
| Cell area | 36 088 | 36 131 | +0.12 % |
| Iterations / runtime | | | 7 / 229 s |
| Acceptance rate | | | 80 % |

Accepted here: `balanced_mux_tree` on `rv_alu` and on `dsp_mux_chain`,
`pipeline_insert` on `dsp_mac`, `logic_restructure` on `crc32_par`.
`register_retime` on `dsp_mac` was proved equivalent and rejected on timing.

## The full metric set

Everything the project brief asks to report, and where to find it:

| Metric | Where |
|---|---|
| WNS before / after | `metrics.json → wns_ns`, report table |
| TNS before / after | `metrics.json → tns_ns` |
| Fmax before / after | `metrics.json → per_clock[].fmax_*` |
| Number of timing violations before / after | `metrics.json → setup_violations` |
| Worst hold slack before / after | `metrics.json → worst_hold_ns` |
| Cell area before / after | `metrics.json → area` |
| Critical-path clocks closed | `metrics.json → clocks_closed` |
| Patches proposed / accepted | `metrics.json → patches_proposed / patches_accepted` |
| Patch acceptance rate | `metrics.json → acceptance_rate` |
| Formal proofs passed / failed | `metrics.json → proofs_passed / proofs_failed` |
| End-to-end runtime | `metrics.json → runtime_s` |
| Iterations required | `metrics.json → iterations` |
| Rejection reason breakdown | `metrics.json → rejection_reasons` |
| Per-transform proposed / accepted | `metrics.json → by_transform` |
| End-to-end equivalence vs original RTL | `metrics.json → final_equivalence` |

## Reproducing

```bash
python3 -m genrtl check
python3 -m genrtl --bench full baseline
python3 -m genrtl --bench full optimize --name full_run --iters 16 --paths 24
python3 tests/run_all.py
streamlit run dashboard/app.py
```

The heuristic proposal engine is deterministic, so a rerun on the same toolchain
version reproduces these numbers exactly. Swapping in an LLM backend changes
which transformation is tried first, and therefore the path through the search
space — but not the gates, and not what is allowed into the design.
