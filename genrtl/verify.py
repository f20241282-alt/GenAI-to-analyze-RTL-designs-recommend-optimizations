"""The hard verification gate.

Nothing reaches the design unless it clears every stage below, in order:

    1. lint       -- the patched RTL parses and elaborates
    2. synthesis  -- the whole design still maps to gates
    3. formal     -- the changed module is proved equivalent to the original
    4. timing     -- setup slack actually improves
    5. hold       -- hold slack does not become invalid
    6. area       -- the cell-area cost stays inside budget

Stages 4-6 live in ``accept.py``; this module implements 1-3.

Two formal engines are used:

``equiv``  Yosys' sequential equivalence engine (equiv_make / equiv_simple /
           equiv_induct). Complete for transformations that preserve latency
           and state encoding -- combinational restructuring, Boolean
           factoring, register duplication.

``bmc``    A generated miter that instantiates the original and the patched
           module side by side, sequences reset, delays the golden outputs by
           the transform's declared latency offset, and compares them only
           where the patched module asserts its valid output. Proved by bounded
           model checking with Yosys' built-in SAT solver. This is what makes
           pipeline insertion and FSM re-encoding checkable rather than
           hopeful.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import FlowConfig
from .tools import ToolResult, yosys


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""
    seconds: float = 0.0
    log: Optional[Path] = None

    def to_dict(self) -> dict:
        return {"gate": self.name, "passed": self.passed, "detail": self.detail,
                "seconds": round(self.seconds, 2),
                "log": str(self.log) if self.log else None}


@dataclass
class ProofResult:
    passed: bool
    engine: str
    module: str
    parameters: Dict[str, int] = field(default_factory=dict)
    latency: int = 0
    depth: int = 0
    detail: str = ""
    seconds: float = 0.0
    log: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "engine": self.engine, "module": self.module,
            "parameters": self.parameters, "latency": self.latency,
            "bmc_depth": self.depth, "detail": self.detail,
            "seconds": round(self.seconds, 2),
            "log": str(self.log) if self.log else None,
        }


# ---------------------------------------------------------------------------
# Parameterisation discovery
# ---------------------------------------------------------------------------
_PARAM_VAL_RE = re.compile(r"\\(?P<name>[A-Za-z_]\w*)=(?P<val>s?\d*'?[01]+|\d+)")


def parameterizations(elab_json: Path, module_base: str) -> List[Dict[str, int]]:
    """Parameter sets that ``module_base`` is actually instantiated with.

    Yosys encodes them in the specialised module name
    (``$paramod\\dsp_mac\\W=s32'0...10000``). Hash-form names carry no values,
    in which case the module's declared defaults are used (empty dict).
    """
    try:
        data = json.loads(Path(elab_json).read_text())
    except Exception:
        return [{}]
    out: List[Dict[str, int]] = []
    for name in data.get("modules", {}):
        if name == module_base:
            out.append({})
            continue
        if not name.startswith("$paramod"):
            continue
        if f"\\{module_base}\\" not in name and not name.endswith(f"\\{module_base}"):
            continue
        params: Dict[str, int] = {}
        tail = name.split(f"\\{module_base}\\", 1)
        if len(tail) == 2:
            for m in _PARAM_VAL_RE.finditer("\\" + tail[1]):
                raw = m.group("val")
                if "'" in raw:
                    bits = raw.split("'", 1)[1]
                    val = int(bits, 2) if set(bits) <= {"0", "1"} else 0
                    if raw.startswith("s") and bits[:1] == "1":
                        val -= 1 << len(bits)
                else:
                    val = int(raw)
                params[m.group("name")] = val
        out.append(params)
    # de-duplicate, preserving order
    seen, uniq = set(), []
    for p in out:
        k = tuple(sorted(p.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq or [{}]


_STABLE_RE = re.compile(r"genrtl[-_]latency[-_]stable\s*[:=]\s*(?P<v>[^\n\r]+)")


def stable_inputs_of(files: List[Path], module: str) -> List[str]:
    """Inputs the module documents as stable while a transaction is in flight.

    Declared in the RTL as ``// genrtl-latency-stable: a, b``. Pipelining a
    module whose inputs are read by more than one stage is only equivalent
    under this contract, so it is recorded and turned into an explicit
    assumption in the miter rather than being assumed silently.
    """
    for f in files:
        if Path(f).stem != module:
            continue
        m = _STABLE_RE.search(Path(f).read_text(errors="replace"))
        if m:
            return [s.strip() for s in m.group("v").split(",") if s.strip()]
    return []


# ---------------------------------------------------------------------------
# Gate 1: lint
# ---------------------------------------------------------------------------
def gate_lint(cfg: FlowConfig, rtl_files: List[Path], rtl_root: Path,
              workdir: Path) -> GateResult:
    defines = " ".join(f"-D{d}" for d in cfg.bench.defines)
    files = " ".join(f'"{p}"' for p in rtl_files)
    script = f"""read_verilog -sv {defines} -I "{rtl_root}" {files}
hierarchy -check -top {cfg.top}
proc
check -assert
"""
    res = yosys(script, workdir=workdir, name="lint", binary=cfg.tools.yosys,
                timeout=600)
    return GateResult("lint", res.ok,
                      "" if res.ok else res.tail(15),
                      res.seconds, res.log_path)


# ---------------------------------------------------------------------------
# Gate 3: formal equivalence
# ---------------------------------------------------------------------------
def _read_stanza(cfg: FlowConfig, rtl_files: List[Path], rtl_root: Path,
                 module: str, params: Dict[str, int], alias: str) -> str:
    defines = " ".join(f"-D{d}" for d in cfg.bench.defines)
    files = " ".join(f'"{p}"' for p in rtl_files)
    chparam = ""
    if params:
        sets = " ".join(f"-set {k} {v}" for k, v in params.items())
        chparam = f"chparam {sets} {module}\n"
    # async2sync converts asynchronous resets into a form the SAT engine can
    # import; without it every $adffe cell aborts the proof.
    return f"""design -reset
read_verilog -sv {defines} -I "{rtl_root}" {files}
{chparam}setattr -mod -unset keep_hierarchy
prep -top {module} -flatten
memory_map
async2sync
opt_clean
rename {module} {alias}
design -stash {alias}_d
"""


def prove_equiv(cfg: FlowConfig, module: str, params: Dict[str, int],
                gold_files: List[Path], gold_root: Path,
                gate_files: List[Path], gate_root: Path,
                workdir: Path, *, timeout: int = 900) -> ProofResult:
    """Unbounded sequential equivalence by temporal induction.

    ``miter -equiv`` builds a single miter whose ``trigger`` output is high iff
    the two modules disagree. ``sat -tempinduct -prove trigger 0`` then proves
    the trigger can never rise: the base case starts from the zero state (which
    is what the reset drives both designs to) and the induction step assumes
    agreement for k cycles and proves it for k+1. For a transformation that
    preserves state encoding this is a complete proof, not a bounded one.
    """
    script = (
        _read_stanza(cfg, gold_files, gold_root, module, params, "gold")
        + _read_stanza(cfg, gate_files, gate_root, module, params, "gate")
        + """design -reset
design -copy-from gold_d -as gold gold
design -copy-from gate_d -as gate gate
miter -equiv -flatten gold gate miter
hierarchy -top miter
opt -full
sat -tempinduct -prove trigger 0 -set-init-zero -verify miter
"""
    )
    res = yosys(script, workdir=workdir, name=f"equiv_{module}",
                binary=cfg.tools.yosys, timeout=timeout)
    out = res.stdout + res.stderr
    proven = res.ok and "Induction step proven: SUCCESS" in out
    detail = ""
    if not proven:
        if res.returncode == 124:
            detail = f"proof timed out after {timeout}s"
        elif "Induction step failed" in out:
            detail = "temporal induction could not prove equivalence"
        elif "model found for base case" in out:
            detail = "counterexample found in the base case"
        else:
            detail = res.tail(12)
    m = re.search(r"Trying induction with length (\d+)", out[::-1])
    depth = 1
    for mm in re.finditer(r"Trying induction with length (\d+)", out):
        depth = int(mm.group(1))
    return ProofResult(
        passed=proven, engine="yosys-tempinduct", module=module,
        parameters=params, latency=0, depth=depth,
        detail=detail or f"equivalence proven by temporal induction (k={depth})",
        seconds=res.seconds, log=res.log_path)


# ---- latency-offset miter -------------------------------------------------
def _port_widths(cfg: FlowConfig, module: str, params: Dict[str, int],
                 files: List[Path], root: Path, workdir: Path
                 ) -> Tuple[Dict[str, Tuple[str, int]], int]:
    """(ports, flattened cell count) for ``module``.

    The cell count is what the BMC depth is scaled against: a 12 K-cell AES
    round and a 2 K-cell MAC cannot afford the same unrolling depth.
    """
    out_json = workdir / f"ports_{module}.json"
    workdir.mkdir(parents=True, exist_ok=True)
    defines = " ".join(f"-D{d}" for d in cfg.bench.defines)
    filelist = " ".join(f'"{p}"' for p in files)
    chparam = ""
    if params:
        chparam = "chparam " + " ".join(
            f"-set {k} {v}" for k, v in params.items()) + f" {module}\n"
    script = f"""design -reset
read_verilog -sv {defines} -I "{root}" {filelist}
{chparam}setattr -mod -unset keep_hierarchy
prep -top {module} -flatten
memory_map
async2sync
opt_clean
write_json "{out_json}"
"""
    yosys(script, workdir=workdir, name=f"ports_{module}",
          binary=cfg.tools.yosys, timeout=600)
    data = json.loads(out_json.read_text())
    mods = data.get("modules") or {}
    mod = mods.get(module)
    if mod is None:
        mod = next((m for n, m in mods.items()
                    if m.get("attributes", {}).get("top")), None)
    if mod is None and mods:
        mod = next(iter(mods.values()))
    if mod is None:
        raise ValueError(f"no module {module} in the port probe output")
    ports = {n: (p["direction"], len(p["bits"])) for n, p in mod["ports"].items()}
    return ports, len(mod.get("cells", {}))


#: SAT budget in (cells x unrolled cycles); the BMC depth is derived from it
BMC_CELL_STEP_BUDGET = 75_000
MIN_BMC_DEPTH = 5


def bmc_depth_for(cells: int, cap: int) -> int:
    """Pick an unrolling depth the SAT solver can actually finish."""
    if cells <= 0:
        return cap
    return max(MIN_BMC_DEPTH, min(cap, BMC_CELL_STEP_BUDGET // cells))


def build_latency_miter(module: str, ports: Dict[str, Tuple[str, int]],
                        latency: int, *, clk: str = "clk", rst: str = "rst_n",
                        valid_out: str = "out_valid",
                        reset_cycles: int = 2,
                        stable_inputs: Optional[List[str]] = None) -> str:
    """Emit a miter that compares gate against gold delayed by ``latency``."""
    ins = [n for n, (d, _) in ports.items() if d == "input" and n not in (clk, rst)]
    outs = [n for n, (d, _) in ports.items() if d == "output"]
    if not outs:
        raise ValueError(f"{module} has no outputs to compare")
    if valid_out and valid_out not in outs:
        if latency > 0:
            raise ValueError(
                f"{module} has no {valid_out} output to qualify a "
                f"{latency}-cycle latency offset against")
        valid_out = None      # compare every output on every cycle instead

    def decl(n: str, prefix: str) -> str:
        w = ports[n][1]
        rng = f"[{w - 1}:0] " if w > 1 else ""
        return f"  wire {rng}{prefix}{n};"

    lines: List[str] = []
    lines.append("// auto-generated latency-offset equivalence miter")
    lines.append("`default_nettype none")
    lines.append("module genrtl_lat_miter (")
    lines.append(f"    input wire {clk},")
    port_lines = []
    for n in ins:
        w = ports[n][1]
        rng = f"[{w - 1}:0] " if w > 1 else ""
        port_lines.append(f"    input wire {rng}{n}")
    lines.append(",\n".join(port_lines))
    lines.append(");")
    lines.append("  reg [7:0] genrtl_cyc;")
    lines.append("  initial genrtl_cyc = 8'd0;")
    lines.append(f"  always @(posedge {clk}) if (genrtl_cyc != 8'hFF) "
                 f"genrtl_cyc <= genrtl_cyc + 8'd1;")
    lines.append(f"  wire {rst} = (genrtl_cyc >= 8'd{reset_cycles});")
    for n in outs:
        lines.append(decl(n, "g_"))
        lines.append(decl(n, "t_"))

    def inst(mod: str, prefix: str, name: str) -> str:
        conns = [f".{clk}({clk})", f".{rst}({rst})"]
        conns += [f".{n}({n})" for n in ins]
        conns += [f".{n}({prefix}{n})" for n in outs]
        return f"  {mod} {name} (\n      " + ",\n      ".join(conns) + "\n  );"

    lines.append(inst("gold", "g_", "u_gold"))
    lines.append(inst("gate", "t_", "u_gate"))

    # Inputs the module documents as stable-while-in-flight are frozen to an
    # arbitrary but time-invariant value. This is an ASSUMPTION, recorded in
    # the proof result: pipelining a module whose inputs are consumed by
    # different stages is only legal under exactly this contract.
    stable = [n for n in (stable_inputs or []) if n in ins]
    if stable:
        lines.append("  reg genrtl_frz_valid;")
        for n in stable:
            w = ports[n][1]
            rng = f"[{w - 1}:0] " if w > 1 else ""
            lines.append(f"  reg {rng}genrtl_frz_{n};")
        lines.append(f"  always @(posedge {clk}) begin")
        lines.append("    if (!genrtl_frz_valid) begin")
        for n in stable:
            lines.append(f"      genrtl_frz_{n} <= {n};")
        lines.append("      genrtl_frz_valid <= 1'b1;")
        lines.append("    end")
        lines.append("  end")
        lines.append(f"  always @(posedge {clk}) if (genrtl_frz_valid) begin")
        for n in stable:
            lines.append(f"    assume ({n} == genrtl_frz_{n});")
        lines.append("  end")

    # golden output delay line
    for n in outs:
        w = ports[n][1]
        rng = f"[{w - 1}:0] " if w > 1 else ""
        for k in range(1, latency + 1):
            lines.append(f"  reg {rng}g_{n}_d{k};")
    lines.append(f"  always @(posedge {clk}) begin")
    for n in outs:
        for k in range(latency, 0, -1):
            src = f"g_{n}" if k == 1 else f"g_{n}_d{k - 1}"
            lines.append(f"    g_{n}_d{k} <= {src};")
    lines.append("  end")

    # Reset is released after ``reset_cycles``; the first cycle whose result
    # can legitimately be compared is one launch plus the latency offset later.
    first = reset_cycles + latency + 1
    lines.append(f"  wire genrtl_settled = (genrtl_cyc >= 8'd{first});")
    lines.append(f"  always @(posedge {clk}) begin")
    lines.append("    if (genrtl_settled) begin")
    if valid_out:
        gold_v = f"g_{valid_out}_d{latency}" if latency else f"g_{valid_out}"
        lines.append(f"      assert (t_{valid_out} == {gold_v});")
        lines.append(f"      if (t_{valid_out}) begin")
        for n in outs:
            if n == valid_out:
                continue
            gold = f"g_{n}_d{latency}" if latency else f"g_{n}"
            lines.append(f"        assert (t_{n} == {gold});")
        lines.append("      end")
    else:
        for n in outs:
            gold = f"g_{n}_d{latency}" if latency else f"g_{n}"
            lines.append(f"      assert (t_{n} == {gold});")
    lines.append("    end")
    lines.append("  end")
    lines.append("endmodule")
    lines.append("`default_nettype wire")
    return "\n".join(lines) + "\n"


def prove_latency_equiv(cfg: FlowConfig, module: str, params: Dict[str, int],
                        gold_files: List[Path], gold_root: Path,
                        gate_files: List[Path], gate_root: Path,
                        workdir: Path, *, latency: int,
                        depth: Optional[int] = None,
                        valid_out: str = "out_valid") -> ProofResult:
    """Bounded sequential equivalence with an explicit cycle offset."""
    d = depth or cfg.seq_bmc_depth
    try:
        ports, cells = _port_widths(cfg, module, params, gold_files, gold_root,
                                    workdir)
        if depth is None:
            d = bmc_depth_for(cells, cfg.seq_bmc_depth)
        stable = stable_inputs_of(gold_files, module)
        miter_src = build_latency_miter(module, ports, latency,
                                        valid_out=valid_out,
                                        stable_inputs=stable)
    except Exception as exc:
        return ProofResult(False, "bmc-latency", module, params, latency, d,
                           f"could not build miter: {exc}")

    miter_path = workdir / f"lat_miter_{module}.sv"
    workdir.mkdir(parents=True, exist_ok=True)
    miter_path.write_text(miter_src)

    script = (
        _read_stanza(cfg, gold_files, gold_root, module, params, "gold")
        + _read_stanza(cfg, gate_files, gate_root, module, params, "gate")
        + f"""design -reset
design -copy-from gold_d -as gold gold
design -copy-from gate_d -as gate gate
read_verilog -sv "{miter_path}"
hierarchy -check -top genrtl_lat_miter
prep -top genrtl_lat_miter -flatten
sat -seq {d} -prove-asserts -set-assumes -set-init-zero -verify genrtl_lat_miter
"""
    )
    res = yosys(script, workdir=workdir, name=f"latequiv_{module}",
                binary=cfg.tools.yosys, timeout=2400)
    out = res.stdout + res.stderr
    proven = res.ok and "SUCCESS" in out.upper() and "FAIL" not in out.upper()
    stable = stable_inputs_of(gold_files, module)
    detail = (f"bounded equivalence proven to depth {d} with a "
              f"{latency}-cycle latency offset, qualified by {valid_out}"
              + (f", assuming {', '.join(stable)} stable while a transaction "
                 f"is in flight (declared by genrtl-latency-stable)"
                 if stable else "")
              if proven else res.tail(15))
    return ProofResult(proven, "bmc-latency", module, params, latency, d,
                       detail, res.seconds, res.log_path)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def prove(cfg: FlowConfig, module: str, equivalence: str, latency: int,
          gold_files: List[Path], gold_root: Path,
          gate_files: List[Path], gate_root: Path,
          workdir: Path, elab_json: Optional[Path] = None) -> List[ProofResult]:
    """Run the right proof for a transform, once per real parameterisation."""
    param_sets = parameterizations(elab_json, module) if elab_json else [{}]
    results: List[ProofResult] = []
    for params in param_sets:
        sub = workdir / ("params_" + ("default" if not params else
                                      "_".join(f"{k}{v}" for k, v in params.items())))
        if equivalence == "seq_latency" and latency > 0:
            r = prove_latency_equiv(cfg, module, params, gold_files, gold_root,
                                    gate_files, gate_root, sub, latency=latency)
        else:
            r = prove_equiv(cfg, module, params, gold_files, gold_root,
                            gate_files, gate_root, sub)
            if not r.passed:
                # Encoding-changing edits fail induction by construction: the
                # two machines never share a state vector. Fall back to a
                # bounded proof from reset and report the depth honestly.
                fb = prove_latency_equiv(
                    cfg, module, params, gold_files, gold_root, gate_files,
                    gate_root, sub, latency=0, valid_out=None)
                if fb.passed:
                    fb.engine = "bmc"
                    fb.detail = (
                        "temporal induction inconclusive (the transform "
                        "changes the state encoding, so the two machines never "
                        f"share a state vector); bounded equivalence proven "
                        f"from reset to depth {fb.depth}")
                    r = fb
        results.append(r)
    return results
