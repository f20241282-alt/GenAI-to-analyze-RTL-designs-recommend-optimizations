"""Metric collection -- exactly the list the project brief asks for."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


def _pct(before: float, after: float) -> float:
    if before == 0:
        return 0.0
    return (after - before) / abs(before) * 100.0


def summarize_dict(run: dict) -> dict:
    base = run.get("baseline", {})
    fin = run.get("final", {})
    iters = run.get("iterations", [])

    proposed = len([i for i in iters if i.get("proposal", {}).get("transform")])
    accepted = len([i for i in iters if i.get("accepted")])
    proofs = [p for i in iters for p in i.get("proofs", [])]
    proofs_pass = len([p for p in proofs if p.get("passed")])
    proofs_fail = len(proofs) - proofs_pass

    rejects: Dict[str, int] = {}
    for i in iters:
        if i.get("accepted"):
            continue
        for g in i.get("gates", []):
            if not g.get("passed"):
                rejects[g["gate"]] = rejects.get(g["gate"], 0) + 1
                break
        else:
            if i.get("reason"):
                rejects["no_actionable_path"] = rejects.get(
                    "no_actionable_path", 0) + 1

    by_transform: Dict[str, Dict[str, int]] = {}
    for i in iters:
        t = i.get("proposal", {}).get("transform")
        if not t:
            continue
        rec = by_transform.setdefault(t, {"proposed": 0, "accepted": 0})
        rec["proposed"] += 1
        rec["accepted"] += 1 if i.get("accepted") else 0

    base_clocks = base.get("per_clock", {})
    fin_clocks = fin.get("per_clock", {})
    base_fmax = base.get("fmax_mhz", {})
    fin_fmax = fin.get("fmax_mhz", {})
    per_clock = []
    for c in sorted(set(base_clocks) | set(fin_clocks)):
        b, f = base_clocks.get(c, {}), fin_clocks.get(c, {})
        per_clock.append({
            "clock": c,
            "wns_before": b.get("wns", 0.0), "wns_after": f.get("wns", 0.0),
            "tns_before": b.get("tns", 0.0), "tns_after": f.get("tns", 0.0),
            "violations_before": b.get("violations", 0),
            "violations_after": f.get("violations", 0),
            "fmax_before": base_fmax.get(c, 0.0),
            "fmax_after": fin_fmax.get(c, 0.0),
        })

    closed = [p["clock"] for p in per_clock
              if p["wns_before"] < 0 <= p["wns_after"]]

    final_eq = run.get("final_equivalence", [])

    return {
        "run_dir": run.get("run_dir"),
        "config": run.get("config", {}),
        "wns_ns": {"before": base.get("wns_ns", 0.0),
                   "after": fin.get("wns_ns", 0.0),
                   "delta": fin.get("wns_ns", 0.0) - base.get("wns_ns", 0.0)},
        "tns_ns": {"before": base.get("tns_ns", 0.0),
                   "after": fin.get("tns_ns", 0.0),
                   "delta": fin.get("tns_ns", 0.0) - base.get("tns_ns", 0.0),
                   "pct": _pct(base.get("tns_ns", 0.0), fin.get("tns_ns", 0.0))},
        "worst_hold_ns": {"before": base.get("whs_ns", 0.0),
                          "after": fin.get("whs_ns", 0.0)},
        "setup_violations": {"before": base.get("setup_violations", 0),
                             "after": fin.get("setup_violations", 0)},
        "hold_violations": {"before": base.get("hold_violations", 0),
                            "after": fin.get("hold_violations", 0)},
        "cells": {"before": base.get("cells", 0), "after": fin.get("cells", 0)},
        "area": {"before": base.get("area", 0.0), "after": fin.get("area", 0.0),
                 "pct": _pct(base.get("area", 0.0), fin.get("area", 0.0))},
        "per_clock": per_clock,
        "clocks_closed": closed,
        "iterations": len(iters),
        "patches_proposed": proposed,
        "patches_accepted": accepted,
        "acceptance_rate": (accepted / proposed) if proposed else 0.0,
        "proofs_passed": proofs_pass,
        "proofs_failed": proofs_fail,
        "rejection_reasons": rejects,
        "by_transform": by_transform,
        "final_equivalence_all_proven": bool(final_eq) and all(
            p.get("passed") for p in final_eq),
        "final_equivalence": final_eq,
        "runtime_s": run.get("seconds", 0.0),
        "protected_modules": [p["module"] for p in run.get("protection", [])],
    }


def summarize(run_result) -> dict:
    return summarize_dict(run_result.to_dict())


def write(m: dict, run_dir: Path) -> Path:
    p = Path(run_dir) / "metrics.json"
    p.write_text(json.dumps(m, indent=2))
    return p


def render_table(m: dict) -> str:
    L: List[str] = []
    ap = L.append
    ap("=" * 68)
    ap("  GenAI RTL timing-closure results")
    ap("=" * 68)
    cfg = m.get("config", {})
    ap(f"  benchmark        {cfg.get('bench')} on {cfg.get('platform')}"
       f"   engine: {cfg.get('backend')}")
    ap(f"  runtime          {m['runtime_s']:.0f} s over {m['iterations']} iterations")
    ap("")
    ap(f"  {'metric':22s} {'before':>12s} {'after':>12s} {'delta':>12s}")
    ap("  " + "-" * 62)
    w = m["wns_ns"]
    ap(f"  {'WNS (ns)':22s} {w['before']:12.4f} {w['after']:12.4f} "
       f"{w['delta']:+12.4f}")
    t = m["tns_ns"]
    ap(f"  {'TNS (ns)':22s} {t['before']:12.3f} {t['after']:12.3f} "
       f"{t['delta']:+12.3f}")
    h = m["worst_hold_ns"]
    ap(f"  {'worst hold (ns)':22s} {h['before']:12.4f} {h['after']:12.4f} "
       f"{h['after'] - h['before']:+12.4f}")
    v = m["setup_violations"]
    ap(f"  {'setup violations':22s} {v['before']:12d} {v['after']:12d} "
       f"{v['after'] - v['before']:+12d}")
    hv = m["hold_violations"]
    ap(f"  {'hold violations':22s} {hv['before']:12d} {hv['after']:12d} "
       f"{hv['after'] - hv['before']:+12d}")
    c = m["cells"]
    ap(f"  {'cells':22s} {c['before']:12d} {c['after']:12d} "
       f"{c['after'] - c['before']:+12d}")
    a = m["area"]
    ap(f"  {'cell area':22s} {a['before']:12.0f} {a['after']:12.0f} "
       f"{a['pct']:+11.2f}%")
    ap("")
    ap(f"  {'clock':16s} {'period':>8s} {'WNS before':>11s} {'WNS after':>11s} "
       f"{'Fmax before':>12s} {'Fmax after':>11s}")
    ap("  " + "-" * 74)
    for p in m["per_clock"]:
        ap(f"  {p['clock']:16s} {'':>8s} {p['wns_before']:11.4f} "
           f"{p['wns_after']:11.4f} {p['fmax_before']:12.1f} "
           f"{p['fmax_after']:11.1f}")
    ap("")
    ap(f"  patches proposed     {m['patches_proposed']}")
    ap(f"  patches accepted     {m['patches_accepted']}")
    ap(f"  acceptance rate      {m['acceptance_rate'] * 100:.1f}%")
    ap(f"  formal proofs passed {m['proofs_passed']}")
    ap(f"  formal proofs failed {m['proofs_failed']}")
    ap(f"  clocks closed        "
       f"{', '.join(m['clocks_closed']) if m['clocks_closed'] else 'none'}")
    ap(f"  final equivalence    "
       f"{'PROVEN against the original RTL' if m['final_equivalence_all_proven'] else 'not proven / nothing changed'}")
    if m["rejection_reasons"]:
        ap(f"  rejections by gate   " + ", ".join(
            f"{k}={v}" for k, v in sorted(m["rejection_reasons"].items())))
    if m["by_transform"]:
        ap("")
        ap(f"  {'transform':24s} {'proposed':>9s} {'accepted':>9s}")
        ap("  " + "-" * 44)
        for k, v in sorted(m["by_transform"].items(),
                           key=lambda kv: -kv[1]["accepted"]):
            ap(f"  {k:24s} {v['proposed']:9d} {v['accepted']:9d}")
    ap(f"  protected modules    {', '.join(m['protected_modules'])}")
    ap("=" * 68)
    return "\n".join(L)
