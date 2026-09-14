# SETUP — install, run, and understand GenRTL

This walks you from a bare Windows 11 machine to a working setup, in two stages:

* **Stage A — test in 30 seconds, no install.** The optimisation **advisor**
  (`suggest`) runs on nothing but Python, on any OS. Start here.
* **Stage B — the full measured flow.** `baseline` / `paths` / `optimize` need
  **Yosys** + **OpenSTA**, which on Windows are cleanest inside **WSL2 (Ubuntu)**.

| you want to… | you need | command |
|---|---|---|
| suggest optimisations for any RTL | just Python | `python -m genrtl suggest <file>` |
| measure real STA before/after + prove patches | Yosys + OpenSTA (via WSL2) | `python -m genrtl optimize` |

---

## Stage A — test right now (Windows, no toolchain)

Your machine already has Python at `C:\Python314`. The `python` command on PATH
may be the Windows Store stub, so use the **`py`** launcher.

Run the toolchain-free demo:

```bash
py demo.py
```

Suggest optimisations for the bundled complex examples:

```bash
py -m genrtl suggest examples\complex_accel.sv
```

```bash
py -m genrtl suggest examples\crypto_core.sv examples\bus_arbiter.sv
```

Suggest optimisations for **your own** RTL, and save a Markdown review:

```bash
py -m genrtl suggest path\to\your_design.sv --format md --out review.md
```

Run the pure-Python tests:

```bash
py tests\run_all.py --fast
```

Useful `suggest` flags: `--min-severity medium`, `--only pipelining,fsm_encoding`,
`--auto-only` (only the formally-verifiable rewrites), `--format json`,
`--narrate --llm anthropic` (adds an LLM review; needs `ANTHROPIC_API_KEY`).

### Complex test designs included

| file | what it stresses |
|---|---|
| `examples/complex_accel.sv` | 2-clock accelerator: mux tree, adder tree, MAC pipelining, dense FSM, boolean factor, **multi-bit CDC hazard**, blocking-in-clocked bug |
| `examples/crypto_core.sv` | keyed hash round: long XOR chain, strength reduction, comparator decode, un-pipelined multiplier, common sub-expression |
| `examples/bus_arbiter.sv` | 4-master arbiter: dense FSM, resource sharing (2 multipliers), CSE, latch inference, non-blocking-in-combinational bug, incomplete sensitivity |
| `examples/fir4_unpipelined.sv`, `opcode_alu.sv`, `vending_fsm.sv` | smaller focused examples |

---

## Stage B — install Yosys + OpenSTA (the full flow)

These are Linux-native open-source tools. On Windows, run them in **WSL2**. This
is the reliable path — a native Windows OpenSTA build is not readily available.

### B1. Install WSL2 + Ubuntu

In **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot if prompted, then launch **Ubuntu** from the Start menu and set a Linux
username/password when it first opens. Everything from here runs in that Ubuntu
shell.

### B2. Get the repository into Linux

The repo is visible from WSL at `/mnt/c/...`. Copy it into your Linux home for
speed (builds and synthesis are much faster on the native filesystem):

```bash
cp -r "/mnt/c/Users/CSB/Downloads/Astera Labs Repository" ~/genrtl
cd ~/genrtl
```

### B3. Install the toolchain (one script)

```bash
sudo ./setup/install_tools.sh
```

This takes ~10–20 minutes and installs, under `/usr/local` and `/opt/pdk`:

* **yosys**, **iverilog**, **verilator** (apt)
* **CUDD** + **OpenSTA** (built from source)
* **Nangate45** (setup/hold/typ corners) and **SKY130 HD** standard-cell libs
* the Python packages in `requirements.txt` (streamlit, plotly, pandas)

### B4. Verify

```bash
python3 -m genrtl check
```

You want to see `yosys` and `sta (OpenSTA)` with paths, and both platforms
marked `ok`:

```
tools
  yosys            /usr/bin/yosys
  sta (OpenSTA)    /usr/local/bin/sta
platforms
  nangate45    ok       /opt/pdk/nangate45/Nangate45_typ.lib
```

---

## Stage B — run the measured flow

```bash
# 1. baseline: does the benchmark really miss its timing? (yes, on purpose)
python3 -m genrtl --bench full baseline

# 2. see the worst paths and the exact RTL slice each maps back to
python3 -m genrtl --bench full paths --top 3 --slice

# 3. run the closed loop: propose -> verify -> accept/revert, repeatedly
python3 -m genrtl --bench full optimize --name full_run --iters 14

# 4. rebuild the report / metrics from a finished run
python3 -m genrtl report --name full_run

# 5. interactive dashboard (also has an "Analyze any RTL" tab)
streamlit run dashboard/app.py
```

Outputs land in `runs/full_run/`: `report.md` (human report), `metrics.json`
(every number the brief asks for), `iterations.csv`, and `patches/*.diff` (the
accepted/rejected patches). `run.json` is the full machine record.

Use `--bench small` for a fast (~21K-cell) loop while you experiment, and add
`--llm anthropic` (with `ANTHROPIC_API_KEY` exported) to use a real model
instead of the built-in heuristic proposer.

`suggest` works in WSL too — and there it can additionally be cross-checked
against the real flow.

---

## How it works

The system is a **closed loop with the model on a leash**: the LLM only ever
proposes *which* catalogued change to try; synthesis, timing and formal
verification decide whether it lives.

```
 RTL + SDC constraints
      │
      ▼
 (1) Yosys synthesis ───────────────► gate-level netlist + `src` line annotations
      │
      ▼
 (2) OpenSTA ───────────────────────► worst setup/hold paths, WNS/TNS, per-clock Fmax
      │
      ▼
 (3) Localisation ──────────────────► map the failing path back to a 50–120 line
      │                                RTL slice + a "why it's slow" diagnosis
      ▼
 (4) GenAI proposer ────────────────► {transform, parameters} chosen from a fixed
      │                                catalogue of 8 safe transforms (LLM or heuristic)
      ▼
 (5) Verified rewriter ─────────────► applies that transform to a scratch copy
      │
      ▼
 (6) THE GATE, in order:
        CDC-protect → lint → synth → FORMAL EQUIVALENCE → setup↑ → hold-safe → area≤budget
        │                                    │
        └── any failure ► revert + log       └── proof: temporal induction (latency-preserving)
                                                       or bounded model checking with a
                                                       latency offset (pipelining)
      │
      ▼
 (7) Accept ► repeat on the next worst path, until timing closes or the budget ends
```

The four ideas that make it safe:

1. **Localisation** — a 50K-cell design never enters a prompt. OpenSTA gives the
   failing endpoint; Yosys `src` attributes map it to a line range; the model
   sees a slice. (`genrtl/localize.py`)
2. **Curated safe transforms** — the model can't emit arbitrary RTL, only pick a
   transform + parameters. A verified rewriter I wrote produces the patch.
   (`genrtl/transforms.py`, `genrtl/proposer.py`)
3. **A hard verification gate** — nothing is accepted without passing formal
   equivalence, a real timing improvement, and hold/area checks.
   (`genrtl/verify.py`, `genrtl/accept.py`)
4. **CDC protection** — synchronisers, FIFOs, handshakes and clock-gen are
   do-not-touch, found by four independent detectors (including a structural one
   that catches unlabelled synchronisers). (`genrtl/protect.py`)

The **advisor** (`genrtl/advisor.py`) is the same idea without the tools: it runs
the transform detectors plus a broader set of pattern detectors to *recommend*
optimisations for any RTL, in seconds, with no synthesis. It's steps (3)–(4)
made standalone.

For depth, read `docs/` (one file per topic) and `PROJECT_OVERVIEW.md` (maps
every project deliverable to a file).

---

## Troubleshooting

| symptom | fix |
|---|---|
| `Python was not found` on Windows | use `py` instead of `python`, or install Python 3 from python.org and tick "Add to PATH" |
| `py` not found | run `C:\Python314\python.exe` directly |
| `genrtl check` says tools `NOT FOUND` on Windows | expected — the measured flow needs WSL2 (Stage B); the advisor still works |
| `platform … MISSING` in WSL | the `.lib` files didn't download; re-run `sudo ./setup/install_tools.sh` (needs internet) |
| OpenSTA build fails | ensure the apt build-deps from step 1 of the script installed; re-run the script (it resumes) |
| the loop is slow | use `--bench small`, or fewer `--iters` |
| want a real LLM | `export ANTHROPIC_API_KEY=…` then add `--llm anthropic` |
| WSL is slow on `/mnt/c` | copy the repo into `~` (Linux home), as in B2 |
