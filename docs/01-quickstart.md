# 01 — Quickstart

## 0. No toolchain? Start here

The optimisation **advisor** needs nothing but Python 3.9+ — no Yosys, no
OpenSTA — and runs on any OS in seconds:

```bash
python demo.py                                   # toolchain-free demo
python -m genrtl suggest examples/               # suggestions for the demo RTL
python -m genrtl suggest path/to/your_design.sv  # suggestions for YOUR RTL
python tests/run_all.py --fast                   # pure-Python tests
```

On Windows use the `py` launcher if `python` isn't on PATH (`py demo.py`), or
run `run_demo.ps1`. See [docs/10](10-any-rtl-advisor.md).

The rest of this document is the **measured** flow, which additionally needs
Yosys and OpenSTA.

## 1. Install the toolchain

```bash
sudo ./setup/install_tools.sh
```

Installs Yosys, Icarus Verilog and Verilator from apt, builds CUDD and OpenSTA
from source, and downloads the Nangate45 (three corners) and SKY130 HD standard
cell libraries into `/opt/pdk`. Takes roughly 10–20 minutes, most of it the
OpenSTA build.

Verify:

```bash
python3 -m genrtl check
```

```
tools
  yosys            /usr/bin/yosys
  sta (OpenSTA)    /usr/local/bin/sta
  verilator        /usr/bin/verilator
  iverilog         /usr/bin/iverilog

platforms
  nangate45    ok       /opt/pdk/nangate45/Nangate45_typ.lib
  sky130hd     ok       /opt/pdk/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib
```

If a library sits elsewhere, set `GENRTL_PDK_ROOT`.

## 2. Confirm the benchmark really fails

```bash
python3 -m genrtl --bench full baseline
```

This runs Yosys synthesis and OpenSTA and prints per-clock WNS, TNS, violation
counts and achievable frequency. The `full` configuration is around 50 K
standard cells and violates setup timing in six of its ten clock domains. Use
`--bench small` (about 21 K cells) when you want the loop to iterate faster.

## 3. See what the optimiser sees

```bash
python3 -m genrtl --bench full paths --top 3 --slice
```

For each of the worst paths this prints the startpoint, endpoint, capture
clock, slack, the RTL module the path maps back to, the exact source lines, a
diagnosis of *why* the path is slow, and the candidate transformations the
structural matcher found. `--slice` also prints the RTL slice that would be
handed to the model.

## 4. Run the closed loop

```bash
python3 -m genrtl --bench full optimize --name full_run --iters 14
```

Each iteration prints one line per gate decision:

```
[02] clk_dsp slack -1.5784 -> pipeline_insert on dsp_mac (heuristic)
     formal: bmc-latency -- bounded equivalence proven to depth 24 with a
             1-cycle latency offset, qualified by out_valid
     ACCEPT -- WNS +0.7294 ns, TNS +41.9434 ns, hold +0.0055 ns, area +1.55%
```

Outputs land in `runs/<name>/`:

| File | Contents |
|---|---|
| `run.json` | Everything: per-iteration paths, proposals, gates, proofs, verdicts |
| `metrics.json` | The metric set from the project brief |
| `report.md` | Human-readable report with every patch inlined |
| `iterations.csv` | One row per iteration, for a spreadsheet |
| `patches/` | Every patch, accepted and rejected, as a unified diff |
| `rtl/` | The optimised RTL |
| `golden/` | An untouched copy, used for the final end-to-end proof |

## 5. Dashboard

```bash
pip install -r requirements.txt
streamlit run dashboard/app.py
```

Pick a run in the sidebar. Five tabs: timing progression, per-iteration detail
with the patch diff, the verification record, the protected-logic set, and all
patches.

## 6. Use a real LLM

The default proposal engine is a deterministic ranker so that the loop runs
with no API key. To put a model in the loop:

```bash
export ANTHROPIC_API_KEY=...
python3 -m genrtl --bench full optimize --llm anthropic --name full_llm

# or
export OPENAI_API_KEY=...
python3 -m genrtl --bench full optimize --llm openai

# or a local model
python3 -m genrtl --bench full optimize --llm ollama
```

`GENRTL_LLM_MODEL` overrides the model name. Nothing else changes: the same
prompt, the same catalogue, the same gates.

## Useful flags

```bash
--bench small|full          benchmark size
--platform nangate45|sky130hd
--iters N                   iteration limit
--paths N                   worst paths pulled from STA per iteration
--area-budget 0.10          maximum fractional cell-area growth
--sdc path/to/other.sdc     different constraints
```

## Re-generating reports

```bash
python3 -m genrtl report --name full_run
```

Rebuilds `metrics.json`, `report.md`, `iterations.csv` and `patches/` from an
existing `run.json` without re-running any tools.
