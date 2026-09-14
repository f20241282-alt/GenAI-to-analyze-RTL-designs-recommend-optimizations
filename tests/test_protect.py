"""The do-not-touch set must be right, and each detector must work alone."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genrtl import protect                     # noqa: E402
from genrtl.config import REPO                 # noqa: E402
from genrtl.netlist import Netlist             # noqa: E402

RTL = REPO / "rtl"
EXPECTED = {"cdc_sync_2ff", "cdc_pulse_sync", "cdc_handshake",
            "cdc_async_fifo", "nebula_clkgen"}


def _elab():
    """Any elaborated netlist produced by a previous run."""
    return next((p for p in (REPO / "runs").rglob("elab.json")), None)


# These tests validate protection against a real elaborated netlist, which only
# exists after a synthesis run. Without Yosys they skip; the structural detector
# is covered toolchain-free in tests/test_protect_structural.py.
def test_every_cdc_and_clock_module_is_protected():
    elab = _elab()
    if elab is None:
        return                          # no baseline run yet; skip
    ps = protect.build(Netlist.load(elab, "nebula_top"), RTL)
    missing = EXPECTED - set(ps.modules)
    assert not missing, f"not protected: {sorted(missing)}"


def test_datapath_modules_are_not_protected():
    elab = _elab()
    if elab is None:
        return
    ps = protect.build(Netlist.load(elab, "nebula_top"), RTL)
    for m in ("rv_alu", "dsp_mac", "dsp_adder_chain", "ctrl_fsm", "aes_round"):
        assert not ps.is_module_protected(m), f"{m} should be transformable"


def test_structural_detector_finds_the_synchroniser_on_its_own():
    """Rename the module and move it: only the structural rule can fire."""
    elab = _elab()
    if elab is None:
        return
    ps = protect.build(Netlist.load(elab, "nebula_top"), RTL)
    reasons = ps.reasons_for("cdc_sync_2ff")
    assert any(r.startswith("structural:") for r in reasons), reasons
    assert any("flop synchroniser chain" in r for r in reasons), reasons


def test_every_detector_reports_itself():
    elab = _elab()
    if elab is None:
        return
    ps = protect.build(Netlist.load(elab, "nebula_top"), RTL)
    kinds = {r.split(":")[0] for m in ps.modules
             for r in ps.reasons_for(m)}
    for kind in ("path", "name", "annotation", "structural"):
        assert kind in kinds, f"no module was protected by the {kind} detector"


def test_patch_target_check_blocks_protected_files():
    elab = _elab()
    if elab is None:
        return
    ps = protect.build(Netlist.load(elab, "nebula_top"), RTL)
    bad = protect.check_patch_targets(ps, [RTL / "cdc" / "cdc_sync_2ff.sv"])
    assert bad and "protected" in bad[0]
    good = protect.check_patch_targets(ps, [RTL / "core" / "rv_alu.sv"])
    assert not good


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
