"""Every transformation must produce RTL that elaborates.

For each module in the benchmark, detect all sites, apply each one, and run the
patched file through Yosys elaboration. A transform whose output does not
elaborate would burn an iteration in the loop, so this is the cheapest possible
guard.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genrtl import transforms as T          # noqa: E402
from genrtl.config import REPO              # noqa: E402

RTL = REPO / "rtl"

# module -> parameter values it is instantiated with in the full configuration
PARAMS = {
    "aes_round": {"ROUNDS": 2},
    "dsp_adder_chain": {"W": 16},
    "dsp_mux_chain": {"W": 32},
    "fanout_hub": {"FANOUT": 64},
}


def _modules(path: Path):
    return re.findall(r"^\s*module\s+([A-Za-z_]\w*)", path.read_text(),
                      re.MULTILINE)


def _elaborates(work: Path, module: str) -> tuple[bool, str]:
    files = " ".join(f'"{p}"' for p in sorted(work.rglob("*.sv")))
    script = (f'read_verilog -sv -DNEBULA_CFG_FULL -I "{work}" {files}\n'
              f"hierarchy -check -top {module}\nproc\ncheck -assert\n")
    ys = work / "_elab.ys"
    ys.write_text(script)
    r = subprocess.run(["yosys", "-q", "-s", str(ys)],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout)[-400:]


def test_all_detected_transforms_elaborate():
    if shutil.which("yosys") is None:
        return                          # Yosys not installed; skip elaboration
    checked = 0
    failures = []
    for f in sorted(RTL.rglob("*.sv")):
        for module in _modules(f):
            ctx = T.TransformContext.build(f, module, PARAMS.get(module))
            if ctx is None:
                continue
            for site in T.detect_all(ctx):
                with tempfile.TemporaryDirectory() as td:
                    work = Path(td) / "rtl"
                    shutil.copytree(RTL, work)
                    target = work / f.relative_to(RTL)
                    cand = T.TransformSite(**{**site.__dict__, "file": target})
                    try:
                        target.write_text(
                            T.apply_site(cand, PARAMS.get(module)))
                    except T.TransformError as exc:
                        failures.append(
                            f"{module}/{site.transform}: apply raised {exc}")
                        continue
                    ok, log = _elaborates(work, module)
                    checked += 1
                    if not ok:
                        failures.append(
                            f"{module}/{site.transform} {site.params}: {log}")
    assert checked >= 8, f"expected at least 8 sites, checked {checked}"
    assert not failures, "\n".join(failures)


def test_every_catalogued_transform_has_a_site_somewhere():
    """The benchmark must exercise the whole catalogue."""
    seen = set()
    for f in sorted(RTL.rglob("*.sv")):
        for module in _modules(f):
            ctx = T.TransformContext.build(f, module, PARAMS.get(module))
            if ctx is None:
                continue
            for site in T.detect_all(ctx):
                seen.add(site.transform)
    # register_retime only becomes available after pipeline_insert has run,
    # so it is exercised in test_formal / the loop rather than here.
    expected = set(T.ORDER) - {"register_retime"}
    missing = expected - seen
    assert not missing, f"no site found for: {sorted(missing)}"


def test_protected_modules_are_never_transform_targets():
    from genrtl import protect
    from genrtl.netlist import Netlist
    elab = next((p for p in (REPO / "runs").rglob("elab.json")), None)
    if elab is None:
        return                      # nothing synthesised yet; skip
    ps = protect.build(Netlist.load(elab, "nebula_top"), RTL)
    for f in sorted((RTL / "cdc").rglob("*.sv")) + sorted((RTL / "clk").rglob("*.sv")):
        assert ps.is_file_protected(f), f"{f} is not protected"
        for module in _modules(f):
            assert ps.is_module_protected(module), f"{module} is not protected"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
