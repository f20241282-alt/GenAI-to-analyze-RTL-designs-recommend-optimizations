# GenRTL — Constraint optimisation through RTL enhancement using generative AI

Give it RTL and hard timing constraints. It finds the paths that violate them,
uses a GenAI engine to **recommend and apply RTL optimisations**, and **proves
every applied rewrite safe** before it is allowed into the design.

The rule the whole system is built on: **the model only proposes; the tools
judge.** A patch that fails formal equivalence, doesn't actually improve timing,
or breaks hold, is thrown away and logged. That is what stops a language model
from silently breaking a chip.

There are **two ways in**, for two different needs:

| | command | needs | gives you |
|---|---|---|---|
| **Advisor** — *recommend optimisations for any RTL* | `python -m genrtl suggest <file/dir>` | **just Python** | a ranked, explained set of optimisation suggestions for any RTL, in seconds |
| **Closed loop** — *measure & prove on a benchmark* | `python -m genrtl optimize` | Yosys + OpenSTA | real STA before/after numbers with every applied patch formally proven |

```
RTL + SDC ─► Yosys synthesis ─► OpenSTA ─► critical paths
                                              │
                     STA-driven RTL localisation (50–120 line slice)
                                              │
                        GenAI engine: pick transform + parameters
                                              │
                                    candidate RTL patch
                                              │
        lint ─► synthesis ─► formal equivalence ─► STA ─► accept / revert
                                              │
                                    repeat until closure
```

---

> **New here?** Read **[SETUP.md](SETUP.md)** — install, run, and how it works,
> from a bare Windows machine (WSL2 for Yosys/OpenSTA) to the full flow.

## Quickstart (no toolchain — works on Windows/macOS/Linux)

The advisor needs nothing but Python 3.9+. From the repo root:

```bash
python demo.py                                   # full toolchain-free demo
python -m genrtl suggest examples/               # suggestions for the demo RTL
python -m genrtl suggest path/to/your_design.sv  # suggestions for YOUR RTL
python tests/run_all.py --fast                   # the pure-Python tests
```

On Windows PowerShell use the launcher if `python` isn't on PATH:

```powershell
py demo.py
py -m genrtl suggest examples\opcode_alu.sv
```

Example output:

```
  !! [    ] opcode_alu:47  blocking_in_sequential/high
        blocking '=' inside a clocked block
        | is_arith_q = is_arith_c;   // BUG: blocking '=' in a sequential block
        fix: Use the non-blocking operator `<=` for every register assignment ...
   * [AUTO] opcode_alu:29  combinational_depth/medium
        balanced_mux_tree: Convert a priority selection chain into a balanced mux tree
        gain: ~2.7x logic-depth reduction on the matched cone
        verified transform: balanced_mux_tree  (proof: comb)
```

`[AUTO]` findings are rewrites the verified engine can apply **and formally
prove equivalent**; the rest are advisory recommendations. See
[docs/10](docs/10-any-rtl-advisor.md).

## Full measured flow (needs Yosys + OpenSTA)

```bash
sudo ./setup/install_tools.sh          # Yosys, OpenSTA, Nangate45 + SKY130
python -m genrtl check                  # confirm tools + libraries
python -m genrtl --bench full baseline  # does the benchmark violate its SDC? (yes)
python -m genrtl --bench full paths --top 3 --slice   # worst paths -> RTL slices
python -m genrtl --bench full optimize --name full_run --iters 14
streamlit run dashboard/app.py          # dashboard (also has an "Analyze any RTL" tab)
```

Everything runs **with no API key** — the proposer falls back to a deterministic
ranker that uses the same evidence the LLM gets. To use a real model:

```bash
export ANTHROPIC_API_KEY=...
python -m genrtl --bench full optimize --llm anthropic --name full_llm
python -m genrtl suggest my_core/ --narrate --llm anthropic
```

---

## What is in here

| path | contents |
|---|---|
| `rtl/` | The benchmark: **5 asynchronous master clocks**, **5 generated clocks** (÷2/3/4/5/8), real CDC (async FIFO, handshake, pulse-sync, 2-flop sync), **~50 K standard cells** in the full configuration |
| `examples/` | Standalone RTL (not the benchmark) to demonstrate the advisor on arbitrary designs |
| `constraints/` | Tight SDC the baseline does **not** meet |
| `genrtl/` | The engine: synthesis, STA, localisation, protection, transforms, **advisor**, formal, acceptance, loop |
| `dashboard/` | Streamlit dashboard (run view + "Analyze any RTL") |
| `tests/` | Self-checks, including formal negative controls and pure-Python advisor/protection tests |
| `docs/` | One document per topic |
| `results/` | Committed reference runs (`full_run`, `small_run`) with metrics, patches and reports |

## The four principles

1. **Localisation** — a 50 K-cell design never goes into a prompt. OpenSTA gives
   the failing path's start/endpoint; Yosys `src` annotations map those back to
   a 50–120 line slice. [docs/03](docs/03-flow-and-localisation.md)
2. **Curated safe transforms** — the model does not write RTL. It picks one of
   eight catalogued transformations and supplies its parameters; a verified
   rewriter produces the patch. [docs/04](docs/04-transform-library.md)
3. **A hard verification gate** — CDC check → lint → synthesis → formal
   equivalence → setup improvement → hold safety → area budget. First failure
   reverts. [docs/05](docs/05-verification-gate.md)
4. **Explicit CDC protection** — synchronisers, async FIFOs, handshakes and the
   clock generator are do-not-touch, found by four independent detectors, one of
   which finds unlabelled synchronisers structurally.
   [docs/06](docs/06-cdc-protection.md)

## Reference result (Nangate45, full benchmark)

| metric | before | after |
|---|---|---|
| WNS | −7.73 ns | **−0.88 ns** |
| TNS | −1316.7 ns | **−104.7 ns (−92%)** |
| Worst hold slack | +5.5 ps | +5.5 ps (unchanged, 0 hold violations) |
| Cell area | 82,716 | 83,497 (**+0.94%**) |

5 of 8 proposed patches accepted, every one formally proven equivalent. Full
numbers and honest limitations: [docs/08](docs/08-metrics-and-results.md),
[docs/09](docs/09-limitations.md).

## Documentation

| Document | Topic |
|---|---|
| [01 Quickstart](docs/01-quickstart.md) | Install, run, read the output |
| [02 Benchmark](docs/02-benchmark.md) | The multi-clock design and its constraints |
| [03 Flow and localisation](docs/03-flow-and-localisation.md) | Synthesis, STA, mapping paths back to RTL |
| [04 Transform library](docs/04-transform-library.md) | The eight transformations and their preconditions |
| [05 Verification gate](docs/05-verification-gate.md) | How each patch is proved |
| [06 CDC protection](docs/06-cdc-protection.md) | The do-not-touch set |
| [07 GenAI engine](docs/07-genai-engine.md) | Prompt, backends, constrained output |
| [08 Metrics and results](docs/08-metrics-and-results.md) | What is measured and what was measured |
| [09 Limitations](docs/09-limitations.md) | What this does not do, honestly |
| [10 Any-RTL advisor](docs/10-any-rtl-advisor.md) | Suggest optimisations for any RTL, no toolchain |

See also **[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)** for how every deliverable
in the project brief maps onto this repository.
