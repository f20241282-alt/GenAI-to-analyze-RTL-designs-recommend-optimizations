#!/usr/bin/env python3
"""Test runner with no external dependencies.

    python3 tests/run_all.py            # everything, including the SAT proofs
    python3 tests/run_all.py --fast     # skip the slow formal tests
    python3 tests/run_all.py -k mux     # only tests whose name matches

Also runs under pytest if you prefer it.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

MODULES = ["test_rtlparse", "test_advisor", "test_protect_structural",
           "test_protect", "test_transforms", "test_sim", "test_formal"]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true",
                    help="skip tests that invoke the SAT solver")
    ap.add_argument("-k", default="", help="only run matching test names")
    a = ap.parse_args()

    passed = failed = skipped = 0
    t_all = time.time()

    for name in MODULES:
        path = HERE / f"{name}.py"
        if not path.is_file():
            continue
        try:
            mod = load(name)
        except Exception:
            print(f"\n\033[31m!! could not import {name}\033[0m")
            traceback.print_exc()
            failed += 1
            continue

        slow = getattr(mod, "SLOW", False)
        print(f"\n\033[1m{name}\033[0m" + ("  (slow)" if slow else ""))
        if slow and a.fast:
            print("  skipped (--fast)")
            skipped += len(getattr(mod, "TESTS", []))
            continue

        for fn in getattr(mod, "TESTS", []):
            if a.k and a.k not in fn.__name__:
                skipped += 1
                continue
            t0 = time.time()
            try:
                fn()
                dt = time.time() - t0
                print(f"  \033[32mPASS\033[0m {fn.__name__}  ({dt:.1f}s)")
                passed += 1
            except AssertionError as exc:
                dt = time.time() - t0
                print(f"  \033[31mFAIL\033[0m {fn.__name__}  ({dt:.1f}s)")
                for line in str(exc).splitlines()[:12]:
                    print(f"        {line}")
                failed += 1
            except Exception:
                dt = time.time() - t0
                print(f"  \033[31mERROR\033[0m {fn.__name__}  ({dt:.1f}s)")
                traceback.print_exc()
                failed += 1

    print(f"\n{'=' * 60}")
    print(f"  {passed} passed, {failed} failed, {skipped} skipped "
          f"in {time.time() - t_all:.1f}s")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
