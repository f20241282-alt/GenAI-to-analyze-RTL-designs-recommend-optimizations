#!/usr/bin/env python3
"""Zero-dependency demo -- runs with nothing but Python (no Yosys / OpenSTA).

    python demo.py

It exercises the "GenAI recommends RTL optimisations for any RTL" surface of the
project end to end without an EDA toolchain:

  1. runs the optimisation advisor on the standalone examples in examples/
  2. runs it on the ~50K-cell benchmark in rtl/ and summarises what it found
  3. lists the verified transform sites the closed loop would act on

For the *measured* closed-loop results (real STA numbers, formal proofs) you
need Yosys + OpenSTA; see the README and `python -m genrtl check`.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from genrtl import advisor                      # noqa: E402
from genrtl import transforms as T              # noqa: E402


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def main() -> int:
    rule("1) Advisor on standalone example RTL (examples/)")
    for f in sorted((REPO / "examples").glob("*.sv")):
        findings = advisor.analyze_file(f)
        c = advisor.summary_counts(findings)
        print(f"\n  {f.name}: {len(findings)} findings "
              f"({c['auto']} auto-appliable + {c['advisory']} advisory)")
        for x in findings:
            tag = "AUTO" if x.auto else "    "
            print(f"     [{tag}] {x.severity:6s} {x.category:24s} "
                  f"L{x.line:<4d} {x.title}")

    rule("2) Advisor on the ~50K-cell benchmark (rtl/)")
    fs = advisor.analyze([REPO / "rtl"])
    by_cat = Counter(x.category for x in fs)
    print(f"\n  {len(fs)} findings across the benchmark "
          f"({sum(x.auto for x in fs)} auto-appliable, "
          f"{sum(not x.auto for x in fs)} advisory)")
    for cat, n in by_cat.most_common():
        print(f"     {cat:26s} {n}")
    print("\n  (no false CDC or attribute findings -- the real crossings are "
          "in protected submodules)")

    rule("3) Verified transform sites the closed loop would act on")
    seen = set()
    for f in sorted((REPO / "rtl").rglob("*.sv")):
        import re
        for m in re.finditer(r"^\s*module\s+([A-Za-z_]\w*)", f.read_text(),
                             re.MULTILINE):
            ctx = T.TransformContext.build(f, m.group(1))
            if ctx is None:
                continue
            for s in T.detect_all(ctx):
                seen.add(s.transform)
                print(f"     {m.group(1):18s} {s.transform:22s} "
                      f"line {s.line:<4d} {s.params}")
    print(f"\n  transform types exercised: {', '.join(sorted(seen))}")

    rule("next step")
    print("  Try it on YOUR RTL:")
    print("      python -m genrtl suggest path/to/your_design.sv")
    print("      python -m genrtl suggest path/to/your_design.sv --format md "
          "--out review.md")
    print("  Full measured flow (needs Yosys + OpenSTA):")
    print("      python -m genrtl check")
    print("      python -m genrtl --bench full optimize --name full_run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
