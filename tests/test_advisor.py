"""The standalone advisor must find the right things on known RTL.

These tests run with nothing but the Python standard library -- no Yosys, no
OpenSTA -- so the "suggest optimisations for any RTL" surface is covered even on
a machine with no EDA toolchain installed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genrtl import advisor as A          # noqa: E402
from genrtl.config import REPO           # noqa: E402

EX = REPO / "examples"
RTL = REPO / "rtl"


def _cats(findings):
    return {(f.module, f.category) for f in findings}


def test_examples_exist():
    for name in ("fir4_unpipelined.sv", "opcode_alu.sv", "vending_fsm.sv"):
        assert (EX / name).is_file(), f"missing example {name}"


def test_fir_finds_adder_tree_and_strength_reduction():
    fs = A.analyze_file(EX / "fir4_unpipelined.sv")
    cats = _cats(fs)
    assert ("fir4_unpipelined", "combinational_depth") in cats
    assert ("fir4_unpipelined", "strength_reduction") in cats
    assert ("fir4_unpipelined", "pipelining") in cats
    # the adder tree must be an auto-appliable, verified transform
    tree = [f for f in fs if f.category == "combinational_depth"]
    assert any(f.auto and f.transform == "balanced_adder_tree" for f in tree)


def test_opcode_alu_finds_blocking_bug_and_mux_tree():
    fs = A.analyze_file(EX / "opcode_alu.sv")
    cats = _cats(fs)
    assert ("opcode_alu", "blocking_in_sequential") in cats
    assert ("opcode_alu", "comparator_chain") in cats
    assert any(f.auto and f.transform == "balanced_mux_tree" for f in fs)
    # the blocking-assignment finding is high severity
    blk = next(f for f in fs if f.category == "blocking_in_sequential")
    assert blk.severity == "high"


def test_vending_fsm_finds_onehot_and_latch():
    fs = A.analyze_file(EX / "vending_fsm.sv")
    cats = _cats(fs)
    assert any(f.auto and f.transform == "fsm_reencode" for f in fs)
    assert ("vending_fsm", "latch_inference") in cats


def test_benchmark_has_no_false_cdc_or_attribute_findings():
    """The real CDC crossings go through submodules, and (* keep *) is not an
    expression -- the advisor must not flag either on the benchmark."""
    fs = A.analyze([RTL])
    assert not [f for f in fs if f.category == "cdc_multibit"], \
        "advisor false-flagged a CDC hazard inside a single module"
    for f in fs:
        if f.category == "common_subexpression":
            assert "*" not in f.title, "advisor matched a (* attribute *) as CSE"


def test_benchmark_yields_auto_and_advisory():
    fs = A.analyze([RTL])
    assert sum(1 for f in fs if f.auto) >= 8, "expected the verified transforms"
    assert sum(1 for f in fs if not f.auto) >= 1, "expected advisory findings too"


def test_renderers_and_json_are_well_formed():
    fs = A.analyze([EX])
    assert A.render_text(fs).strip()
    assert A.render_markdown(fs).lstrip().startswith("#")
    data = json.loads(A.to_json(fs))
    assert data["summary"]["auto"] + data["summary"]["advisory"] == len(fs)
    for f in data["findings"]:
        for key in ("category", "severity", "module", "line", "suggestion"):
            assert key in f


def test_finding_lines_point_into_the_file():
    for name in ("fir4_unpipelined.sv", "opcode_alu.sv", "vending_fsm.sv"):
        path = EX / name
        n = len(path.read_text().splitlines())
        for f in A.analyze_file(path):
            assert 1 <= f.line <= n, f"{name}: line {f.line} out of range"
            assert f.snippet, f"{name}: empty snippet at line {f.line}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
