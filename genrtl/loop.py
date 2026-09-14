"""The closed loop.

    RTL + SDC
        -> Yosys synthesis
        -> OpenSTA
        -> critical paths
        -> STA-driven RTL localisation
        -> GenAI proposal (transform + parameters)
        -> candidate patch
        -> lint / synthesis
        -> formal equivalence      -- fail: revert and log
        -> OpenSTA re-analysis
        -> acceptance rule         -- fail: revert and log
        -> accept, repeat

Working copies
--------------
``<run>/rtl``        the accepted design; only a patch that clears every gate
                     is ever promoted into it
``<run>/candidate``  a scratch copy the current patch is applied to
``<run>/golden``     an untouched copy of the original RTL, used for the final
                     end-to-end equivalence check
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import accept, localize, protect, proposer, sta as sta_mod, synth, verify
from . import transforms as T
from .config import REPO, FlowConfig
from .netlist import Netlist


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class IterationRecord:
    iteration: int
    path: dict
    localization: dict
    proposal: dict
    gates: List[dict] = field(default_factory=list)
    proofs: List[dict] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    accepted: bool = False
    reason: str = ""
    diff: str = ""
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class RunResult:
    run_dir: Path
    baseline: dict
    final: dict
    iterations: List[IterationRecord] = field(default_factory=list)
    protection: List[dict] = field(default_factory=list)
    final_equivalence: List[dict] = field(default_factory=list)
    seconds: float = 0.0
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_dir": str(self.run_dir),
            "config": self.config,
            "baseline": self.baseline,
            "final": self.final,
            "protection": self.protection,
            "final_equivalence": self.final_equivalence,
            "iterations": [i.to_dict() for i in self.iterations],
            "seconds": round(self.seconds, 1),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mirror(files: List[Path], src_root: Path, dst_root: Path) -> List[Path]:
    return [dst_root / p.relative_to(src_root) for p in files]


def _copy_rtl(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _unified_diff(a: Path, b: Path, label: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        a.read_text().splitlines(keepends=True),
        b.read_text().splitlines(keepends=True),
        fromfile=f"a/{label}", tofile=f"b/{label}"))


def _metrics(s: sta_mod.StaResult, cells: int, area: float) -> dict:
    m = s.summary()
    m.update({"cells": cells, "area": area})
    return m


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
class Optimizer:
    def __init__(self, cfg: FlowConfig, run_dir: Path,
                 backend_name: Optional[str] = None,
                 verbose: bool = True):
        self.cfg = cfg
        self.run_dir = run_dir
        self.verbose = verbose
        self.backend = proposer.make_backend(backend_name)
        self.rtl_root = run_dir / "rtl"
        self.cand_root = run_dir / "candidate"
        self.golden_root = run_dir / "golden"
        self.sdc_text = Path(cfg.sdc).read_text()
        #: transform site keys already tried on a given endpoint
        self.tried: Dict[str, Set[str]] = {}
        #: every site key tried anywhere -- many endpoints share one root cause,
        #: so retrying the identical rewrite on each of them is wasted work
        self.tried_global: Set[str] = set()
        #: cumulative extra latency introduced per module
        self.latency: Dict[str, int] = {}
        self.history: List[dict] = []

    # -- plumbing ----------------------------------------------------------
    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def _files(self, root: Path) -> List[Path]:
        return _mirror(self.cfg.rtl_files(), REPO / "rtl", root)

    def _synth_sta(self, root: Path, workdir: Path, tag: str
                   ) -> Tuple[synth.SynthResult, Optional[sta_mod.StaResult]]:
        sr = synth.synthesize(self.cfg, self._files(root), root, workdir,
                              name=f"synth_{tag}")
        if not sr.ok:
            return sr, None
        st = sta_mod.analyze(self.cfg, sr.netlist, workdir, name=f"sta_{tag}")
        return sr, st

    # -- main --------------------------------------------------------------
    def run(self, max_iters: Optional[int] = None) -> RunResult:
        t0 = time.time()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _copy_rtl(REPO / "rtl", self.rtl_root)
        _copy_rtl(REPO / "rtl", self.golden_root)

        # ---- baseline ----------------------------------------------------
        self.log("=== baseline synthesis + STA ===")
        base_wd = self.run_dir / "iter_000_baseline"
        sr, st = self._synth_sta(self.rtl_root, base_wd, "base")
        if st is None:
            raise RuntimeError(f"baseline synthesis failed:\n{sr.log}")
        baseline = _metrics(st, sr.cells, sr.area)
        self.log(f"    cells={sr.cells}  area={sr.area:.0f}  "
                 f"WNS={st.wns:+.4f}  TNS={st.tns:+.2f}  "
                 f"WHS={st.whs:+.4f}  violations={st.setup_violations}")

        nl_elab = Netlist.load(sr.elab_json, self.cfg.top)
        protection = protect.build(nl_elab, self.rtl_root)
        self.log(f"    protected modules: "
                 f"{', '.join(sorted(protection.modules)) or 'none'}")

        result = RunResult(run_dir=self.run_dir, baseline=baseline, final={},
                           protection=protection.summary(),
                           config={
                               "platform": self.cfg.platform.name,
                               "bench": self.cfg.bench.name,
                               "top": self.cfg.top,
                               "sdc": str(self.cfg.sdc),
                               "backend": (self.backend.name if self.backend
                                           else "heuristic"),
                               "max_iters": max_iters or self.cfg.max_iters,
                               "min_wns_gain_ns": self.cfg.min_wns_gain_ns,
                               "max_area_growth": self.cfg.max_area_growth,
                           })

        cur_synth, cur_sta = sr, st
        n_iters = max_iters or self.cfg.max_iters
        dry = 0

        for it in range(1, n_iters + 1):
            if cur_sta.wns >= 0:
                self.log(f"=== timing closed (WNS {cur_sta.wns:+.4f}) -- stopping ===")
                break
            rec = self._iterate(it, cur_synth, cur_sta, protection)
            result.iterations.append(rec)
            # Two consecutive iterations with nothing left to try means the
            # catalogue is exhausted for the paths that remain.
            dry = dry + 1 if not rec.proposal.get("transform") else 0
            if dry >= 2:
                self.log("=== no catalogued transformation applies to any "
                         "remaining violating path -- stopping ===")
                break
            if rec.accepted:
                cur_wd = self.run_dir / f"iter_{it:03d}" / "accepted"
                sr2 = synth.synthesize(self.cfg, self._files(self.rtl_root),
                                       self.rtl_root, cur_wd, name="synth_acc")
                st2 = sta_mod.analyze(self.cfg, sr2.netlist, cur_wd, name="sta_acc")
                cur_synth, cur_sta = sr2, st2
                nl_elab = Netlist.load(sr2.elab_json, self.cfg.top)

        # ---- final state --------------------------------------------------
        final_wd = self.run_dir / "final"
        fsr, fst = self._synth_sta(self.rtl_root, final_wd, "final")
        result.final = _metrics(fst, fsr.cells, fsr.area) if fst else {}
        result.final_equivalence = self._final_equivalence(fsr)
        result.seconds = time.time() - t0

        (self.run_dir / "run.json").write_text(
            json.dumps(result.to_dict(), indent=2, default=str))
        self.log(f"=== done in {result.seconds:.0f}s -- "
                 f"{self.run_dir / 'run.json'} ===")
        return result

    # -- one iteration ------------------------------------------------------
    def _iterate(self, it: int, cur_synth: synth.SynthResult,
                 cur_sta: sta_mod.StaResult,
                 protection: protect.ProtectionSet) -> IterationRecord:
        t0 = time.time()
        wd = self.run_dir / f"iter_{it:03d}"
        wd.mkdir(parents=True, exist_ok=True)

        nl_map = Netlist.load(cur_synth.mapped_json, self.cfg.top)
        nl_elab = Netlist.load(cur_synth.elab_json, self.cfg.top)
        module_params = self._module_params(cur_synth, nl_elab)

        # ---- pick the worst path that still has something untried ---------
        chosen_loc = None
        proposal = None
        for path in cur_sta.setup_paths:
            if path.slack >= 0:
                continue
            loc = localize.localize(path, nl_map, nl_elab, protection,
                                    self.rtl_root)
            key = path.endpoint
            tried = self.tried.setdefault(key, set())
            if loc.blocked:
                self.log(f"[{it:02d}] skip {path.endpoint} -- {loc.blocked_reason}")
                continue
            if len(tried) >= self.cfg.max_attempts_per_path:
                continue
            p = proposer.propose(loc, cur_sta, protection, self.sdc_text, it,
                                 self.history, self.backend,
                                 tried | self.tried_global, module_params)
            if p.ok:
                chosen_loc, proposal = loc, p
                break
            self.log(f"[{it:02d}] no proposal for {path.endpoint} -- "
                     f"{p.error or p.explanation}")

        if chosen_loc is None or proposal is None or proposal.site is None:
            rec = IterationRecord(
                iteration=it, path={}, localization={},
                proposal={"error": "no actionable path left"},
                accepted=False,
                reason="every violating path is protected, exhausted, or "
                       "matches no catalogued transformation",
                seconds=time.time() - t0)
            self.log(f"[{it:02d}] {rec.reason}")
            return rec

        site = proposal.site
        path = chosen_loc.path
        self.tried[path.endpoint].add(site.key())
        self.tried_global.add(site.key())
        self.log(f"[{it:02d}] {path.end_clock} slack {path.slack:+.4f} -> "
                 f"{site.transform} on {site.module} "
                 f"({proposal.backend})")

        rec = IterationRecord(
            iteration=it, path=path.to_dict(),
            localization=chosen_loc.to_dict(),
            proposal={**proposal.to_dict(), "site": site.to_dict()},
            metrics_before=_metrics(cur_sta, cur_synth.cells, cur_synth.area))

        # ---- apply the patch to a scratch copy ----------------------------
        _copy_rtl(self.rtl_root, self.cand_root)
        rel = site.file.relative_to(self.rtl_root)
        cand_file = self.cand_root / rel
        try:
            cand_site = T.TransformSite(**{**site.__dict__, "file": cand_file})
            cand_file.write_text(
                T.apply_site(cand_site, module_params.get(site.module)))
        except Exception as exc:
            rec.gates.append({"gate": "apply", "passed": False,
                              "detail": str(exc)})
            rec.reason = f"patch generation failed: {exc}"
            rec.seconds = time.time() - t0
            self.log(f"     REJECT -- {rec.reason}")
            return self._finish(rec)

        rec.diff = _unified_diff(self.rtl_root / rel, cand_file, str(rel))
        (wd / "patch.diff").write_text(rec.diff)

        # ---- gate 1: protection ------------------------------------------
        viol = protect.check_patch_targets(protection, [self.rtl_root / rel])
        rec.gates.append({"gate": "cdc_protection", "passed": not viol,
                          "detail": "; ".join(viol) or
                                    "patch touches no protected file"})
        if viol:
            rec.reason = "; ".join(viol)
            rec.seconds = time.time() - t0
            self.log(f"     REJECT -- {rec.reason}")
            return self._finish(rec)

        # ---- gate 2: lint -------------------------------------------------
        g = verify.gate_lint(self.cfg, self._files(self.cand_root),
                             self.cand_root, wd / "lint")
        rec.gates.append(g.to_dict())
        if not g.passed:
            rec.reason = "patched RTL does not elaborate"
            rec.seconds = time.time() - t0
            self.log(f"     REJECT -- {rec.reason}")
            return self._finish(rec)

        # ---- gate 3: synthesis -------------------------------------------
        csr = synth.synthesize(self.cfg, self._files(self.cand_root),
                               self.cand_root, wd / "synth", name="synth_cand")
        rec.gates.append({"gate": "synthesis", "passed": csr.ok,
                          "detail": "" if csr.ok else csr.log,
                          "seconds": round(csr.seconds, 2)})
        if not csr.ok:
            rec.reason = "synthesis of the patched design failed"
            rec.seconds = time.time() - t0
            self.log(f"     REJECT -- {rec.reason}")
            return self._finish(rec)

        # ---- gate 4: formal equivalence ----------------------------------
        proofs = verify.prove(
            self.cfg, site.module, site.equivalence, site.latency_delta,
            self._files(self.rtl_root), self.rtl_root,
            self._files(self.cand_root), self.cand_root,
            wd / "formal", elab_json=cur_synth.elab_json)
        rec.proofs = [p.to_dict() for p in proofs]
        proven = bool(proofs) and all(p.passed for p in proofs)
        rec.gates.append({
            "gate": "formal_equivalence", "passed": proven,
            "detail": "; ".join(p.detail for p in proofs)[:400]})
        if not proven:
            rec.reason = "formal equivalence failed"
            rec.seconds = time.time() - t0
            self.log(f"     REJECT -- {rec.reason}: "
                     f"{'; '.join(p.detail for p in proofs)[:120]}")
            return self._finish(rec)
        self.log(f"     formal: {proofs[0].engine} -- {proofs[0].detail[:90]}")

        # ---- gate 5: timing re-analysis ----------------------------------
        cst = sta_mod.analyze(self.cfg, csr.netlist, wd / "synth",
                              name="sta_cand")
        rec.gates.append({"gate": "sta", "passed": cst.ok,
                          "detail": cst.error,
                          "seconds": round(cst.seconds, 2)})
        if not cst.ok:
            rec.reason = "timing re-analysis failed"
            rec.seconds = time.time() - t0
            self.log(f"     REJECT -- {rec.reason}")
            return self._finish(rec)

        rec.metrics_after = _metrics(cst, csr.cells, csr.area)

        verdict = accept.evaluate(self.cfg, cur_sta, cst, cur_synth.area,
                                  csr.area, target_clock=path.end_clock)
        rec.verdict = verdict.to_dict()
        rec.gates.append({"gate": "acceptance", "passed": verdict.accepted,
                          "detail": verdict.summary})
        rec.accepted = verdict.accepted
        rec.reason = verdict.summary
        rec.seconds = time.time() - t0

        if verdict.accepted:
            _copy_rtl(self.cand_root, self.rtl_root)
            if site.latency_delta:
                self.latency[site.module] = (
                    self.latency.get(site.module, 0) + site.latency_delta)
            self.log(f"     ACCEPT -- {verdict.summary}")
        else:
            self.log(f"     REJECT -- {verdict.summary}")
        return self._finish(rec)

    def _module_params(self, sr: synth.SynthResult,
                       nl_elab: Netlist) -> Dict[str, Dict[str, int]]:
        """RTL module -> the parameter values it is instantiated with.

        Only the first parameterisation is used for pattern matching; the
        formal gate still proves every parameterisation separately.
        """
        out: Dict[str, Dict[str, int]] = {}
        if not sr.ok or sr.elab_json is None:
            return out
        for base in {m for m in nl_elab.rtl_modules()}:
            sets = verify.parameterizations(sr.elab_json, base)
            for s in sets:
                if s:
                    out[base] = s
                    break
        return out

    def _finish(self, rec: IterationRecord) -> IterationRecord:
        self.history.append({
            "iteration": rec.iteration,
            "transform": (rec.proposal.get("transform")
                          if rec.proposal else None),
            "module": rec.proposal.get("module") if rec.proposal else None,
            "verdict": "ACCEPTED" if rec.accepted else "REJECTED",
            "reason": rec.reason[:160],
        })
        return rec

    # -- end-to-end proof ---------------------------------------------------
    def _final_equivalence(self, final_synth: synth.SynthResult) -> List[dict]:
        """Prove the finished RTL against the untouched original, per module."""
        changed: List[str] = []
        for p in self.cfg.rtl_files():
            rel = p.relative_to(REPO / "rtl")
            a, b = self.golden_root / rel, self.rtl_root / rel
            if a.read_text() != b.read_text():
                changed.append(rel.as_posix())
        if not changed:
            return []
        self.log(f"=== final equivalence vs the original RTL "
                 f"({len(changed)} changed file(s)) ===")
        out: List[dict] = []
        for rel in changed:
            module = Path(rel).stem
            lat = self.latency.get(module, 0)
            proofs = verify.prove(
                self.cfg, module, "seq_latency" if lat else "seq", lat,
                self._files(self.golden_root), self.golden_root,
                self._files(self.rtl_root), self.rtl_root,
                self.run_dir / "final_formal" / module,
                elab_json=final_synth.elab_json if final_synth.ok else None)
            for pr in proofs:
                d = pr.to_dict()
                d["file"] = rel
                d["cumulative_latency"] = lat
                out.append(d)
            ok = all(p.passed for p in proofs)
            self.log(f"    {module:22s} {'PROVEN' if ok else 'NOT PROVEN'} "
                     f"(latency +{lat})")
        return out


def run(cfg: FlowConfig, run_dir: Path, max_iters: Optional[int] = None,
        backend_name: Optional[str] = None, verbose: bool = True) -> RunResult:
    return Optimizer(cfg, run_dir, backend_name, verbose).run(max_iters)
