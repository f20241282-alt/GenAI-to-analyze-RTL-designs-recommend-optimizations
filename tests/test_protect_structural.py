"""Detector 4 (structural synchroniser detection) in isolation, no Yosys.

Uses a tiny hand-written Yosys-style JSON netlist with two look-alike modules:

  * ``sync2`` -- a genuine 2-flop synchroniser: input -> ff1 -> ff2 -> output,
    both flops enable-free, and ff1's Q feeds *only* ff2.
  * ``pipe2`` -- the same flop chain, but ff1's Q ALSO feeds a combinational
    gate (a datapath), which is exactly what tells a pipeline apart from a
    synchroniser.

The structural detector must protect the first and leave the second alone. Its
name (``sync2``) is deliberately chosen NOT to match any naming pattern, so a
positive result can only come from the structural detector.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genrtl import protect                       # noqa: E402
from genrtl.netlist import Netlist               # noqa: E402
from genrtl.config import REPO                   # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "synth_min.json"


def _ps(top="sync2"):
    nl = Netlist.load(FIX, top)
    return protect.build(nl, REPO / "rtl")


def test_two_flop_synchroniser_is_protected_structurally():
    ps = _ps()
    assert ps.is_module_protected("sync2"), "genuine synchroniser not protected"
    reasons = " ".join(ps.reasons_for("sync2"))
    assert "structural" in reasons, f"protected for the wrong reason: {reasons}"


def test_pipeline_flops_are_not_mistaken_for_a_synchroniser():
    ps = _ps()
    assert not ps.is_module_protected("pipe2"), \
        "a pipeline register was wrongly flagged as a synchroniser"


def test_name_detector_did_not_fire_on_sync2():
    """Prove the protection came from the structural detector, not the name."""
    assert not any(p.search("sync2") for p in
                   [__import__("re").compile(x)
                    for x in protect.PROTECTED_NAME_PATTERNS])


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
