"""Negative controls for the formal gate.

A verification gate that never fails is worthless. For each transformation this
proves the correct patch AND a deliberately mutated version of the same patch,
and asserts that the correct one is proved and the mutated one is rejected.

These tests invoke the SAT solver and are slow (minutes). Run the fast subset
with:

    python3 tests/run_all.py --fast
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genrtl import transforms as T, verify                     # noqa: E402
from genrtl.config import BENCHES, PLATFORMS, REPO, FlowConfig  # noqa: E402

RTL = REPO / "rtl"

#: (rtl file, module, transform, a mutation that must be caught)
CASES = [
    ("dsp/dsp_adder_chain.sv", "dsp_adder_chain", "balanced_adder_tree",
     ("(a6 + a7)", "(a6 + a6)")),
    ("dsp/dsp_mux_chain.sv", "dsp_mux_chain", "balanced_mux_tree",
     ("(sel[0] ? (d[15]) : (d[14]))", "(sel[0] ? (d[14]) : (d[15]))")),
    ("core/rv_alu.sv", "rv_alu", "balanced_mux_tree",
     ("(op[0] ? (ror_r) : (rol_r))", "(op[0] ? (rol_r) : (ror_r))")),
    ("ctrl/ctrl_fsm.sv", "ctrl_fsm", "boolean_factor",
     ("in_flight & (status[4]", "in_flight | (status[4]")),
    ("ctrl/ctrl_fsm.sv", "ctrl_fsm", "fsm_reencode",
     ("S_WB    :                                 state_c = S_IDLE;",
      "S_WB    :                                 state_c = S_ERR;")),
    ("ctrl/fanout_hub.sv", "fanout_hub", "resource_duplication",
     ("else if (mode_set) mode_q__dup1 <= mode_in;",
      "else if (mode_set) mode_q__dup1 <= ~mode_in;")),
    ("dsp/dsp_mac.sv", "dsp_mac", "pipeline_insert",
     ("        prod_c_p1 <= prod_c;", "        prod_c_p1 <= prod_c + 1'b1;")),
]

PARAMS = {"aes_round": {"ROUNDS": 2}}


def _cfg():
    return FlowConfig(platform=PLATFORMS["nangate45"], bench=BENCHES["full"])


def _patched_copy(rel: str, module: str, transform: str, mutate=None):
    """Copy the RTL, apply one transform, optionally corrupt the result."""
    tmp = Path(tempfile.mkdtemp(prefix="genrtl_formal_"))
    gate = tmp / "rtl"
    shutil.copytree(RTL, gate)
    f = gate / rel
    ctx = T.TransformContext.build(f, module, PARAMS.get(module))
    sites = [s for s in T.detect_all(ctx) if s.transform == transform]
    assert sites, f"no {transform} site in {module}"
    text = T.apply_site(sites[0], PARAMS.get(module))
    if mutate is not None:
        old, new = mutate
        assert old in text, f"mutation anchor {old!r} not present for {transform}"
        text = text.replace(old, new, 1)
    f.write_text(text)
    return tmp, gate, sites[0]


def _prove(cfg, module, site, gate_root, tag):
    gold_files = cfg.rtl_files()
    gate_files = [gate_root / p.relative_to(RTL) for p in gold_files]
    elab = next((p for p in (REPO / "runs").rglob("elab.json")), None)
    return verify.prove(cfg, module, site.equivalence, site.latency_delta,
                        gold_files, RTL, gate_files, gate_root,
                        Path(tempfile.mkdtemp(prefix=f"genrtl_{tag}_")),
                        elab_json=elab)


def test_correct_patches_are_proved():
    cfg = _cfg()
    failures = []
    for rel, module, transform, _ in CASES:
        tmp, gate, site = _patched_copy(rel, module, transform)
        try:
            res = _prove(cfg, module, site, gate, "ok")
            if not all(r.passed for r in res):
                failures.append(
                    f"{module}/{transform} NOT proved: "
                    + "; ".join(r.detail[:120] for r in res))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    assert not failures, "\n".join(failures)


def test_mutated_patches_are_rejected():
    cfg = _cfg()
    failures = []
    for rel, module, transform, mutation in CASES:
        tmp, gate, site = _patched_copy(rel, module, transform, mutation)
        try:
            res = _prove(cfg, module, site, gate, "neg")
            if all(r.passed for r in res):
                failures.append(
                    f"{module}/{transform}: the formal gate PASSED a mutated "
                    f"patch -- the engine is not catching real bugs")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    assert not failures, "\n".join(failures)


SLOW = True
TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
