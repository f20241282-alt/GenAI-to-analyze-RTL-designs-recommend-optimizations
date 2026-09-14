"""Command line entry point: ``python -m genrtl <command>``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import BENCHES, PLATFORMS, REPO, RUNS_DIR, FlowConfig, discover_tools
from . import (advisor, loop, localize, metrics, protect, proposer, report,
               sta as sta_mod, synth)
from . import transforms as T
from .netlist import Netlist


def _cfg(a: argparse.Namespace) -> FlowConfig:
    plat = PLATFORMS[a.platform]
    if not plat.available:
        sys.exit(f"error: liberty file for platform {a.platform} not found at "
                 f"{plat.lib_map}\n       run setup/install_tools.sh first.")
    cfg = FlowConfig(platform=plat, bench=BENCHES[a.bench])
    if getattr(a, "sdc", None):
        cfg.sdc = Path(a.sdc)
    if getattr(a, "paths", None):
        cfg.n_paths = a.paths
    if getattr(a, "iters", None):
        cfg.max_iters = a.iters
    if getattr(a, "area_budget", None) is not None:
        cfg.max_area_growth = a.area_budget
    return cfg


# ---------------------------------------------------------------------------
def cmd_check(a: argparse.Namespace) -> int:
    t = discover_tools()
    rows = [("yosys", t.yosys), ("sta (OpenSTA)", t.sta),
            ("verilator", t.verilator or ""), ("iverilog", t.iverilog or ""),
            ("eqy", t.eqy or ""), ("sby", t.sby or "")]
    print("tools")
    for n, p in rows:
        print(f"  {n:16s} {p or 'NOT FOUND'}")
    print("\nplatforms")
    for name, plat in PLATFORMS.items():
        mark = "ok" if plat.available else "MISSING"
        print(f"  {name:12s} {mark:8s} {plat.lib_map}")
        if plat.available and plat.corners_degraded:
            print("               note: min/max corners fall back to the "
                  "mapping corner, hold numbers are approximate")
    print("\nbenchmarks")
    for name, b in BENCHES.items():
        print(f"  {name:12s} {b.description}")
    return 0 if (t.yosys and t.sta) else 1


def cmd_baseline(a: argparse.Namespace) -> int:
    cfg = _cfg(a)
    wd = RUNS_DIR / a.name
    sr = synth.synthesize(cfg, cfg.rtl_files(), REPO / "rtl", wd)
    if not sr.ok:
        print(sr.log)
        return 1
    st = sta_mod.analyze(cfg, sr.netlist, wd)
    print(f"cells       {sr.cells}")
    print(f"area        {sr.area:.1f}")
    print(f"WNS         {st.wns:+.4f} ns")
    print(f"TNS         {st.tns:+.3f} ns")
    print(f"worst hold  {st.whs:+.4f} ns")
    print(f"violations  {st.setup_violations} setup / {st.hold_violations} hold")
    print(f"\n{'clock':16s} {'period':>8s} {'WNS':>9s} {'TNS':>10s} "
          f"{'viol':>5s} {'Fmax MHz':>9s}")
    fmax = st.fmax_mhz()
    for c in sorted(st.clock_periods):
        rec = st.per_clock.get(c, {})
        print(f"{c:16s} {st.clock_periods[c]:8.3f} {rec.get('wns', 0.0):9.4f} "
              f"{rec.get('tns', 0.0):10.3f} {rec.get('violations', 0):5d} "
              f"{fmax.get(c, 0.0):9.1f}")
    (wd / "baseline.json").write_text(json.dumps(
        {"cells": sr.cells, "area": sr.area, **st.summary()}, indent=2))
    print(f"\nwritten: {wd / 'baseline.json'}")
    return 0


def cmd_paths(a: argparse.Namespace) -> int:
    cfg = _cfg(a)
    wd = RUNS_DIR / a.name
    sr = synth.synthesize(cfg, cfg.rtl_files(), REPO / "rtl", wd)
    if not sr.ok:
        print(sr.log)
        return 1
    st = sta_mod.analyze(cfg, sr.netlist, wd)
    nl_m = Netlist.load(sr.mapped_json, cfg.top)
    nl_e = Netlist.load(sr.elab_json, cfg.top)
    ps = protect.build(nl_e, REPO / "rtl")
    locs = localize.localize_paths(st.setup_paths, nl_m, nl_e, ps,
                                   REPO / "rtl", limit=a.top)
    for i, l in enumerate(locs, 1):
        p = l.path
        print(f"\n--- path {i} --------------------------------------------")
        print(f"  clock      {p.end_clock} (period "
              f"{st.clock_periods.get(p.end_clock, 0):.3f} ns)")
        print(f"  slack      {p.slack:+.4f} ns   depth {p.depth}")
        print(f"  start      {p.startpoint}")
        print(f"  end        {p.endpoint}")
        print(f"  diagnosis  {l.bottleneck}")
        if l.blocked:
            print(f"  BLOCKED    {l.blocked_reason}")
            continue
        print(f"  module     {l.primary.module_base}")
        print(f"  source     {l.slice_file}:{l.slice_start}-{l.slice_end}")
        print("  delay by module: " + ", ".join(
            f"{k}={v:.3f}" for k, v in sorted(l.delay_by_module.items(),
                                              key=lambda x: -x[1])[:4]))
        if a.slice:
            print("  ---- RTL slice ----")
            print("\n".join("  " + ln for ln in l.numbered_slice().splitlines()))
        sites = proposer.candidate_sites(l)
        for s in sites[:4]:
            print(f"  candidate  {s.transform} on {s.module} "
                  f"(line {s.line}) params={s.params}")
    return 0


def cmd_transforms(a: argparse.Namespace) -> int:
    for t in T.catalogue():
        print(f"{t['name']}")
        print(f"  {t['title']}")
        print(f"  kind        {t['kind']}")
        print(f"  proof       {t['equivalence']}")
        print(f"  params      {json.dumps(t['params'].get('properties', {}))}")
        print(f"  {' '.join(t['description'].split())}\n")
    if a.scan:
        print("=== sites detected in the current RTL ===")
        for f in FlowConfig(platform=PLATFORMS[a.platform],
                            bench=BENCHES[a.bench]).rtl_files():
            import re as _re
            for m in _re.finditer(r"^\s*module\s+([A-Za-z_]\w*)", f.read_text(),
                                  _re.MULTILINE):
                ctx = T.TransformContext.build(f, m.group(1))
                if ctx is None:
                    continue
                for s in T.detect_all(ctx):
                    print(f"  {m.group(1):18s} {s.transform:22s} "
                          f"line {s.line:4d} {s.params}")
    return 0


def cmd_suggest(a: argparse.Namespace) -> int:
    """Suggest optimisations for ANY RTL -- no Yosys / OpenSTA required."""
    paths = [Path(p) for p in (a.paths or ["rtl"])]
    missing = [p for p in paths if not p.exists()]
    if missing:
        sys.exit("error: path(s) not found: " + ", ".join(map(str, missing)))

    findings = advisor.analyze(paths)

    order = {"high": 3, "medium": 2, "low": 1, "info": 0}
    if a.min_severity:
        floor = order.get(a.min_severity, 0)
        findings = [f for f in findings if order.get(f.severity, 0) >= floor]
    if a.only:
        cats = {c.strip() for c in a.only.split(",")}
        findings = [f for f in findings if f.category in cats]
    if a.auto_only:
        findings = [f for f in findings if f.auto]

    fmt = a.format
    if not fmt and a.out:
        fmt = "md" if str(a.out).endswith((".md", ".markdown")) else \
              "json" if str(a.out).endswith(".json") else "text"
    fmt = fmt or "text"

    if fmt == "json":
        text = advisor.to_json(findings)
    elif fmt in ("md", "markdown"):
        text = advisor.render_markdown(findings)
    else:
        text = advisor.render_text(findings)

    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {len(findings)} findings to {a.out}")
    else:
        print(text)

    if a.narrate:
        backend = proposer.make_backend(a.llm)
        eng = backend.name if backend else "heuristic (no LLM backend available)"
        print("\n" + "=" * 78)
        print(f"  Natural-language review  (engine: {eng})")
        print("=" * 78)
        by_mod: dict = {}
        for f in findings:
            by_mod.setdefault((f.file, f.module), []).append(f)
        for (fpath, module), group in by_mod.items():
            src = ""
            try:
                span = advisor.rp.module_body(Path(fpath).read_text(errors="replace"),
                                              module)
                if span:
                    src = Path(fpath).read_text(errors="replace")[span[0]:span[1]]
            except Exception:
                pass
            print(f"\n### {module} ({fpath})\n")
            print(advisor.narrate(module, src, group, backend))
    return 0


def cmd_optimize(a: argparse.Namespace) -> int:
    cfg = _cfg(a)
    run_dir = RUNS_DIR / a.name
    res = loop.run(cfg, run_dir, max_iters=a.iters, backend_name=a.llm)
    m = metrics.summarize(res)
    metrics.write(m, run_dir)
    report.write_all(res, m, run_dir)
    print()
    print(metrics.render_table(m))
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    run_dir = RUNS_DIR / a.name
    data = json.loads((run_dir / "run.json").read_text())
    m = metrics.summarize_dict(data)
    metrics.write(m, run_dir)
    report.write_all_dict(data, m, run_dir)
    print(metrics.render_table(m))
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="genrtl",
        description="GenAI-assisted RTL timing closure with a hard "
                    "verification gate.")
    ap.add_argument("--platform", default="nangate45", choices=list(PLATFORMS))
    ap.add_argument("--bench", default="small", choices=list(BENCHES))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="report tool and library availability")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("baseline", help="synthesise and analyse the benchmark")
    p.add_argument("--name", default="baseline")
    p.add_argument("--paths", type=int, default=20)
    p.add_argument("--sdc")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("paths", help="show critical paths and their RTL slices")
    p.add_argument("--name", default="baseline")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--paths", type=int, default=20)
    p.add_argument("--slice", action="store_true", help="print the RTL slice")
    p.add_argument("--sdc")
    p.set_defaults(fn=cmd_paths)

    p = sub.add_parser("transforms", help="show the transformation catalogue")
    p.add_argument("--scan", action="store_true",
                   help="also scan the RTL for matching sites")
    p.set_defaults(fn=cmd_transforms)

    p = sub.add_parser(
        "suggest",
        help="suggest optimisations for ANY RTL file/dir (no Yosys/OpenSTA)")
    p.add_argument("paths", nargs="*",
                   help="RTL files or directories (default: rtl/)")
    p.add_argument("--format", choices=["text", "md", "json"], default=None,
                   help="output format (default: text, or inferred from --out)")
    p.add_argument("--out", help="write the report to this file")
    p.add_argument("--min-severity", dest="min_severity",
                   choices=["low", "medium", "high"], default=None,
                   help="drop findings below this severity")
    p.add_argument("--only", help="comma-separated categories to keep")
    p.add_argument("--auto-only", action="store_true",
                   help="only the auto-appliable, formally verifiable findings")
    p.add_argument("--narrate", action="store_true",
                   help="add an LLM natural-language review per module")
    p.add_argument("--llm", default=None,
                   help="LLM backend for --narrate: anthropic | openai | ollama")
    p.set_defaults(fn=cmd_suggest)

    p = sub.add_parser("optimize", help="run the closed optimisation loop")
    p.add_argument("--name", default="opt")
    p.add_argument("--iters", type=int, default=12)
    p.add_argument("--paths", type=int, default=20)
    p.add_argument("--llm", default=None,
                   help="anthropic | openai | ollama | heuristic")
    p.add_argument("--area-budget", type=float, default=None,
                   help="max fractional cell-area growth (default 0.10)")
    p.add_argument("--sdc")
    p.set_defaults(fn=cmd_optimize)

    p = sub.add_parser("report", help="rebuild metrics and reports from run.json")
    p.add_argument("--name", default="opt")
    p.set_defaults(fn=cmd_report)

    a = ap.parse_args(argv)
    return a.fn(a)
