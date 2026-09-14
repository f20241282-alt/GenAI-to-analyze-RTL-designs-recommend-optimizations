"""STA-driven RTL localisation.

A 50 K-cell design cannot be put in an LLM prompt, and it should not be. The
only thing the optimiser needs is the small piece of RTL that produced the
failing path. This module turns an OpenSTA path into:

  * the RTL module that owns the endpoint (and the modules the path runs through)
  * a 50-120 line slice of the original source
  * a delay breakdown that says *why* the path is slow -- logic depth, a single
    dominant stage, or fanout

Everything is derived from Yosys ``src`` annotations plus the design hierarchy,
so it keeps working when the RTL changes underneath it.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .netlist import Netlist, ResolvedObject, SrcRef
from .protect import ProtectionSet
from .sta import TimingPath

MAX_SLICE_LINES = 120
CONTEXT_LINES = 25


@dataclass
class ModuleHit:
    instance_path: str
    module: str          # decorated Yosys name
    module_base: str     # RTL name
    file: Optional[Path]
    line_start: int
    line_end: int
    points: int          # how many path points sit inside this module
    protected: bool
    protect_reasons: List[str] = field(default_factory=list)


@dataclass
class Localization:
    path: TimingPath
    hits: List[ModuleHit]
    primary: Optional[ModuleHit]
    slice_file: Optional[Path]
    #: unprotected modules on this path, best transform target first
    candidates: List[ModuleHit] = field(default_factory=list)
    slice_start: int = 0
    slice_end: int = 0
    slice_text: str = ""
    #: incremental delay grouped by RTL module
    delay_by_module: Dict[str, float] = field(default_factory=dict)
    #: heaviest single stage on the path (pin, delay)
    worst_stage: Tuple[str, float] = ("", 0.0)
    bottleneck: str = "unknown"
    blocked: bool = False
    blocked_reason: str = ""

    def numbered_slice(self) -> str:
        if not self.slice_text:
            return ""
        lines = self.slice_text.splitlines()
        return "\n".join(
            f"{self.slice_start + i:5d} | {ln}" for i, ln in enumerate(lines))

    def to_dict(self) -> dict:
        return {
            "endpoint": self.path.endpoint,
            "startpoint": self.path.startpoint,
            "slack": self.path.slack,
            "clock": self.path.end_clock,
            "primary_module": self.primary.module_base if self.primary else None,
            "slice_file": str(self.slice_file) if self.slice_file else None,
            "slice_lines": [self.slice_start, self.slice_end],
            "delay_by_module": self.delay_by_module,
            "worst_stage": {"pin": self.worst_stage[0], "delay": self.worst_stage[1]},
            "bottleneck": self.bottleneck,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "modules": [
                {"instance": h.instance_path, "module": h.module_base,
                 "file": str(h.file) if h.file else None,
                 "lines": [h.line_start, h.line_end],
                 "points": h.points, "protected": h.protected}
                for h in self.hits
            ],
        }


def _classify(path: TimingPath, worst_stage_delay: float,
              total: float, depth: int) -> str:
    """Say what dominates the path: one slow stage, or many stages."""
    if total <= 0:
        return "unknown"
    if worst_stage_delay > 0.35 * total:
        return "single_dominant_stage"
    if depth >= 40:
        return "logic_depth"
    if depth >= 12:
        return "moderate_depth"
    return "load_or_fanout"


def localize(path: TimingPath, nl_mapped: Netlist, nl_elab: Netlist,
             protection: ProtectionSet,
             rtl_root: Path) -> Localization:
    """Map one timing path back to a small slice of RTL."""
    # ---- resolve every point on the path to its owning module -------------
    resolved: List[Tuple[ResolvedObject, float]] = []
    for pin, delay in path.stage_delays():
        try:
            robj = nl_mapped.resolve_pin(pin)
        except Exception:
            continue
        resolved.append((robj, delay))

    counts: Counter = Counter()
    delay_by_inst: Dict[str, float] = {}
    meta: Dict[str, ResolvedObject] = {}
    for robj, delay in resolved:
        key = robj.instance_path
        counts[key] += 1
        delay_by_inst[key] = delay_by_inst.get(key, 0.0) + delay
        meta.setdefault(key, robj)

    hits: List[ModuleHit] = []
    for inst, n in counts.most_common():
        robj = meta[inst]
        src = robj.module_src
        base = robj.module_base
        hits.append(ModuleHit(
            instance_path=inst,
            module=robj.module,
            module_base=base,
            file=src.file if src else None,
            line_start=src.line_start if src else 0,
            line_end=src.line_end if src else 0,
            points=n,
            protected=protection.is_module_protected(base),
            protect_reasons=protection.reasons_for(base),
        ))

    delay_by_module: Dict[str, float] = {}
    for inst, d in delay_by_inst.items():
        base = meta[inst].module_base
        delay_by_module[base] = delay_by_module.get(base, 0.0) + d

    worst_stage = ("", 0.0)
    for robj, delay in resolved:
        if delay > worst_stage[1]:
            worst_stage = (robj.pin, delay)

    total = sum(d for _, d in resolved)
    loc = Localization(
        path=path, hits=hits, primary=None, slice_file=None,
        delay_by_module=delay_by_module, worst_stage=worst_stage,
        bottleneck=_classify(path, worst_stage[1], total, path.depth),
    )

    # ---- pick the primary (optimisable) module ---------------------------
    # The endpoint register's own module wins by default: retiming, pipelining
    # and output-mux restructuring all have to happen where the capturing flop
    # lives. If that module is protected or unresolvable, fall back to the
    # unprotected module that owns the most delay on the path.
    ranked = sorted(hits, key=lambda h: (-delay_by_module.get(h.module_base, 0.0),
                                         -h.points))
    candidates = [h for h in ranked if not h.protected and h.file is not None]

    endpoint_inst = ""
    try:
        endpoint_inst = nl_mapped.resolve_pin(path.endpoint).instance_path
    except Exception:
        pass

    # Modules that carry essentially none of the path delay are not worth
    # transforming, even though the path runs through them. The module that
    # owns the capturing register is always kept.
    if total > 0:
        candidates = [
            h for h in candidates
            if h.instance_path == endpoint_inst
            or delay_by_module.get(h.module_base, 0.0) >= 0.02 * total]

    for h in candidates:
        if h.instance_path == endpoint_inst:
            candidates = [h] + [c for c in candidates if c is not h]
            break

    loc.candidates = candidates
    if not candidates:
        loc.blocked = True
        prot = [h.module_base for h in hits if h.protected]
        loc.blocked_reason = (
            "every module on this path is protected: " + ", ".join(sorted(set(prot)))
            if prot else "no RTL source could be resolved for this path")
        return loc

    primary = candidates[0]
    loc.primary = primary

    # ---- cut the slice ----------------------------------------------------
    f = primary.file
    if f is None or not Path(f).is_file():
        loc.blocked = True
        loc.blocked_reason = f"source file not found for module {primary.module_base}"
        return loc

    text_lines = Path(f).read_text(errors="replace").splitlines()
    start, end = primary.line_start, min(primary.line_end, len(text_lines))

    if end - start + 1 > MAX_SLICE_LINES:
        # Narrow around the endpoint register when the module is large.
        anchor = None
        for robj, _ in reversed(resolved):
            if robj.instance_path == primary.instance_path and robj.leaf_src:
                anchor = robj.leaf_src
                break
        if anchor is not None:
            start = max(primary.line_start, anchor.line_start - CONTEXT_LINES)
            end = min(primary.line_end, anchor.line_end + CONTEXT_LINES,
                      len(text_lines))
        else:
            end = min(start + MAX_SLICE_LINES - 1, len(text_lines))

    loc.slice_file = Path(f)
    loc.slice_start = start
    loc.slice_end = end
    loc.slice_text = "\n".join(text_lines[start - 1:end])
    return loc


def localize_paths(paths: List[TimingPath], nl_mapped: Netlist,
                   nl_elab: Netlist, protection: ProtectionSet,
                   rtl_root: Path, limit: Optional[int] = None
                   ) -> List[Localization]:
    out: List[Localization] = []
    for p in paths[: (limit or len(paths))]:
        out.append(localize(p, nl_mapped, nl_elab, protection, rtl_root))
    return out
