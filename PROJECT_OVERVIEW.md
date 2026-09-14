# Project overview — deliverables map

This project implements **"Constraint Optimization through RTL Enhancement Using
Generative AI"**: given RTL and hard timing constraints, a GenAI engine
recommends and applies RTL optimisations against timing violations, critical
paths, pipelining, FSM encoding and performance — and every applied change is
formally proven equivalent before it is accepted.

Below, each item from the problem brief maps onto where it lives in this
repository.

## Objectives

| Objective | Where |
|---|---|
| Analyse RTL designs against specified timing constraints | `genrtl/synth.py` (Yosys) + `genrtl/sta.py` (OpenSTA) driven by `constraints/nebula.sdc` |
| Identify critical paths and timing violations | `genrtl/sta.py` (path export, WNS/TNS/hold, per-clock) → `python -m genrtl paths` |
| Use GenAI to recommend RTL optimisations (pipelining, logic restructuring, retiming, FSM optimisation, …) | `genrtl/advisor.py` (recommend for **any** RTL) + `genrtl/proposer.py` + `genrtl/transforms.py` (verified rewrites) |
| Evaluate timing, area, and performance improvements | `genrtl/accept.py` + `genrtl/metrics.py` + `genrtl/report.py` |
| Formally verify equivalence between original and optimised RTL | `genrtl/verify.py` (temporal induction + latency-offset BMC) |

## Deliverables

| Deliverable | Where |
|---|---|
| **RTL timing analysis framework** | `genrtl/synth.py`, `genrtl/sta.py`, `genrtl/localize.py`; CLI: `genrtl baseline`, `genrtl paths` |
| **GenAI-based RTL optimisation engine** | `genrtl/proposer.py` (LLM + heuristic backends), `genrtl/transforms.py` (8 verified transforms), `genrtl/advisor.py` (broad recommender), `genrtl/loop.py` (closed loop) |
| **Critical path and timing violation analysis** | `genrtl/sta.py` + `genrtl/localize.py` (path → RTL slice + delay-by-module diagnosis); CLI: `genrtl paths --top N --slice` |
| **Optimised RTL implementation** | produced by `genrtl optimize`; a committed reference is in `results/full_run/optimized_rtl/` and `results/small_run/optimized_rtl/` |
| **Timing, frequency and PPA comparison** | `genrtl/metrics.py` (WNS/TNS/hold/Fmax/cells/area before-vs-after, acceptance rate, proofs) + `genrtl/report.py`; example: `results/full_run/report.md`, `metrics.json`, `iterations.csv` |
| **Formal equivalence verification report** | `genrtl/verify.py`; per-patch proofs and end-to-end equivalence in each run's `run.json` / `report.md`; theory in [docs/05](docs/05-verification-gate.md) |
| **Interactive demo of the optimisation workflow** | `dashboard/app.py` (Streamlit: run view **and** an "Analyze any RTL" tab) + `demo.py` (toolchain-free) + `python -m genrtl suggest` |

## Benchmark requirements

Defined in `rtl/nebula_top.sv` + `rtl/clk/nebula_clkgen.sv`, constrained by
`constraints/nebula.sdc`.

| Requirement | Where / how |
|---|---|
| ≥ 5 independent master **asynchronous** clock domains | `clk_core, clk_dsp, clk_mem, clk_io, clk_sec` — `set_clock_groups -asynchronous` in the SDC puts each in its own island |
| ≥ 1 generated clock per master | `nebula_clkgen` generates `clk_core_div2, clk_dsp_div3, clk_mem_div4, clk_io_div5, clk_sec_div8`, each with `create_generated_clock` |
| Clock Domain Crossings (CDC) | async FIFO ×2 (`cdc_async_fifo`), 4-phase handshake ×2 (`cdc_handshake`), toggle pulse-sync (`cdc_pulse_sync`), 2-flop sync (`cdc_sync_2ff`) |
| Clock divider logic, multiple ratios | ÷2, ÷3, ÷4, ÷5, ÷8 in `nebula_clkgen` |
| ~50 K standard cells | `NEBULA_CFG_FULL` (4 DSP / 3 RV / 3 CRC lanes, AES 2 rounds) in `rtl/nebula_defines.svh`; reference run reports 50,091 cells |

See [docs/02](docs/02-benchmark.md) for the full benchmark description.

## Optimisation types the engine can recommend

**Auto-appliable and formally verifiable (8 catalogued transforms)** —
`balanced_adder_tree`, `logic_restructure`, `balanced_mux_tree`,
`boolean_factor`, `register_retime`, `pipeline_insert`, `fsm_reencode`,
`resource_duplication`.

**Advisory recommendations (any RTL)** — un-pipelined multiplier / MAC,
comparator-OR-chain decoding, priority-chain flattening, operator strength
reduction, resource sharing, common-sub-expression elimination, high-fanout
duplication, and a set of correctness checks: blocking-in-sequential,
non-blocking-in-combinational, latch inference, incomplete sensitivity lists,
and multi-bit CDC hazards.

Full catalogue and detection rules: [docs/04](docs/04-transform-library.md) and
[docs/10](docs/10-any-rtl-advisor.md).

## How to reproduce

```bash
# toolchain-free (any machine)
python demo.py
python -m genrtl suggest examples/ --format md --out review.md
python tests/run_all.py --fast

# measured (needs Yosys + OpenSTA)
python -m genrtl check
python -m genrtl --bench full baseline
python -m genrtl --bench full optimize --name full_run --iters 14
python -m genrtl report --name full_run
```

## Tooling

Verilog/SystemVerilog · Yosys (synthesis, equivalence) · OpenSTA (timing) ·
OpenROAD (optional) · SymbiYosys / Yosys SAT (formal) · Python · LLM backends
(Anthropic / OpenAI / Ollama, with a deterministic heuristic fallback).
