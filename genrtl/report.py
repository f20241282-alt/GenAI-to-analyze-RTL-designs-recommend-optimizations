"""Human-readable run reports: Markdown, CSV and the per-iteration log."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List


def _md_table(rows: List[List[str]], header: List[str]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def markdown(run: dict, m: dict) -> str:
    cfg = m.get("config", {})
    L: List[str] = []
    ap = L.append
    ap("# GenAI RTL timing-closure run report\n")
    ap(f"- benchmark: **{cfg.get('bench')}** on **{cfg.get('platform')}**")
    ap(f"- proposal engine: **{cfg.get('backend')}**")
    ap(f"- constraints: `{cfg.get('sdc')}`")
    ap(f"- iterations: {m['iterations']}, runtime {m['runtime_s']:.0f} s\n")

    ap("## Headline metrics\n")
    ap(_md_table([
        ["WNS (ns)", f"{m['wns_ns']['before']:.4f}", f"{m['wns_ns']['after']:.4f}",
         f"{m['wns_ns']['delta']:+.4f}"],
        ["TNS (ns)", f"{m['tns_ns']['before']:.3f}", f"{m['tns_ns']['after']:.3f}",
         f"{m['tns_ns']['delta']:+.3f} ({m['tns_ns']['pct']:+.1f}%)"],
        ["Worst hold slack (ns)", f"{m['worst_hold_ns']['before']:.4f}",
         f"{m['worst_hold_ns']['after']:.4f}",
         f"{m['worst_hold_ns']['after'] - m['worst_hold_ns']['before']:+.4f}"],
        ["Setup violations", m['setup_violations']['before'],
         m['setup_violations']['after'],
         f"{m['setup_violations']['after'] - m['setup_violations']['before']:+d}"],
        ["Hold violations", m['hold_violations']['before'],
         m['hold_violations']['after'],
         f"{m['hold_violations']['after'] - m['hold_violations']['before']:+d}"],
        ["Cells", m['cells']['before'], m['cells']['after'],
         f"{m['cells']['after'] - m['cells']['before']:+d}"],
        ["Cell area", f"{m['area']['before']:.0f}", f"{m['area']['after']:.0f}",
         f"{m['area']['pct']:+.2f}%"],
    ], ["metric", "before", "after", "delta"]))

    ap("\n## Per clock domain\n")
    ap(_md_table([[
        p["clock"], f"{p['wns_before']:.4f}", f"{p['wns_after']:.4f}",
        f"{p['tns_before']:.3f}", f"{p['tns_after']:.3f}",
        p["violations_before"], p["violations_after"],
        f"{p['fmax_before']:.1f}", f"{p['fmax_after']:.1f}",
    ] for p in m["per_clock"]],
        ["clock", "WNS before", "WNS after", "TNS before", "TNS after",
         "viol before", "viol after", "Fmax before (MHz)", "Fmax after (MHz)"]))

    ap("\n## Optimisation statistics\n")
    ap(_md_table([
        ["Patches proposed", m["patches_proposed"]],
        ["Patches accepted", m["patches_accepted"]],
        ["Acceptance rate", f"{m['acceptance_rate'] * 100:.1f}%"],
        ["Formal proofs passed", m["proofs_passed"]],
        ["Formal proofs failed", m["proofs_failed"]],
        ["Critical-path clocks closed",
         ", ".join(m["clocks_closed"]) or "none"],
        ["End-to-end equivalence vs original RTL",
         "PROVEN" if m["final_equivalence_all_proven"] else "not proven"],
    ], ["statistic", "value"]))

    if m["by_transform"]:
        ap("\n### By transformation\n")
        ap(_md_table([[k, v["proposed"], v["accepted"]]
                      for k, v in sorted(m["by_transform"].items())],
                     ["transform", "proposed", "accepted"]))

    if m["rejection_reasons"]:
        ap("\n### Rejections by gate\n")
        ap(_md_table([[k, v] for k, v in sorted(m["rejection_reasons"].items())],
                     ["gate that rejected", "count"]))

    ap("\n## Protected (do-not-touch) modules\n")
    for p in run.get("protection", []):
        ap(f"- **{p['module']}** — {'; '.join(p['reasons'])}")

    ap("\n## Iteration log\n")
    for it in run.get("iterations", []):
        pr = it.get("proposal", {})
        if not pr.get("transform"):
            ap(f"### Iteration {it['iteration']} — no actionable path\n")
            ap(f"{it.get('reason', '')}\n")
            continue
        p = it.get("path", {})
        verdict = "ACCEPTED" if it.get("accepted") else "REJECTED"
        ap(f"### Iteration {it['iteration']} — {pr['transform']} on "
           f"`{pr.get('module')}` — **{verdict}**\n")
        ap(f"- path: `{p.get('startpoint')}` → `{p.get('endpoint')}`")
        ap(f"- clock `{p.get('end_clock')}`, slack {p.get('slack', 0):+.4f} ns, "
           f"depth {p.get('depth')}")
        ap(f"- diagnosis: {it.get('localization', {}).get('bottleneck')}")
        ap(f"- rationale: {pr.get('explanation', '')}")
        gates = ", ".join(
            f"{g['gate']}={'pass' if g['passed'] else 'FAIL'}"
            for g in it.get("gates", []))
        ap(f"- gates: {gates}")
        for pf in it.get("proofs", []):
            ap(f"- proof ({pf['engine']}): {pf['detail']}")
        ap(f"- verdict: {it.get('reason', '')}\n")
        if it.get("diff"):
            ap("<details><summary>patch</summary>\n")
            ap("```diff")
            ap(it["diff"].rstrip())
            ap("```\n")
            ap("</details>\n")

    if run.get("final_equivalence"):
        ap("\n## Final equivalence against the untouched original RTL\n")
        ap(_md_table([[
            e.get("file"), e.get("module"), e.get("engine"),
            f"+{e.get('cumulative_latency', 0)}",
            "PROVEN" if e.get("passed") else "NOT PROVEN",
            e.get("detail", "")[:90],
        ] for e in run["final_equivalence"]],
            ["file", "module", "engine", "latency", "result", "detail"]))

    return "\n".join(L) + "\n"


def csv_rows(run: dict) -> List[Dict]:
    rows = []
    for it in run.get("iterations", []):
        pr = it.get("proposal", {})
        mb, ma = it.get("metrics_before", {}), it.get("metrics_after", {})
        rows.append({
            "iteration": it["iteration"],
            "clock": it.get("path", {}).get("end_clock", ""),
            "slack_before": it.get("path", {}).get("slack", ""),
            "transform": pr.get("transform", ""),
            "module": pr.get("module", ""),
            "backend": pr.get("backend", ""),
            "accepted": it.get("accepted", False),
            "wns_before": mb.get("wns_ns", ""),
            "wns_after": ma.get("wns_ns", ""),
            "tns_before": mb.get("tns_ns", ""),
            "tns_after": ma.get("tns_ns", ""),
            "hold_before": mb.get("whs_ns", ""),
            "hold_after": ma.get("whs_ns", ""),
            "area_before": mb.get("area", ""),
            "area_after": ma.get("area", ""),
            "reason": it.get("reason", "")[:160],
            "seconds": round(it.get("seconds", 0.0), 1),
        })
    return rows


def write_all_dict(run: dict, m: dict, run_dir: Path) -> None:
    run_dir = Path(run_dir)
    (run_dir / "report.md").write_text(markdown(run, m))
    rows = csv_rows(run)
    if rows:
        with (run_dir / "iterations.csv").open("w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
    patches = run_dir / "patches"
    patches.mkdir(exist_ok=True)
    for it in run.get("iterations", []):
        if it.get("diff"):
            tag = "accepted" if it.get("accepted") else "rejected"
            (patches / f"iter_{it['iteration']:03d}_{tag}.diff").write_text(
                it["diff"])


def write_all(run_result, m: dict, run_dir: Path) -> None:
    write_all_dict(run_result.to_dict(), m, run_dir)
