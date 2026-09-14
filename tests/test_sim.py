"""Simulation cross-check of the latency-changing transform.

Applies ``pipeline_insert`` to ``aes_round``, then runs the original and the
pipelined module side by side under Icarus Verilog with random stimulus and
compares outputs with a one-cycle offset.

This is a cross-check on the formal gate, not a substitute for it. The same
patch that passes here with constant round keys is correctly *rejected* by the
formal engine unless the RTL declares the keys stable while a block is in
flight -- which is exactly the kind of bug simulation is bad at finding.
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
TB = Path(__file__).resolve().parent / "sim" / "tb_latency_equiv.sv"


def _rename(src: Path, dst: Path, mapping: dict) -> None:
    text = src.read_text()
    for old, new in mapping.items():
        text = re.sub(rf"(?<![\w$]){re.escape(old)}(?![\w$])", new, text)
    dst.write_text(text)


def test_aes_pipeline_matches_original_in_simulation():
    if shutil.which("iverilog") is None:
        return                                  # simulator not installed; skip

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        gate_rtl = work / "rtl"
        shutil.copytree(RTL, gate_rtl)

        f = gate_rtl / "crypto" / "aes_round.sv"
        ctx = T.TransformContext.build(f, "aes_round", {"ROUNDS": 2})
        sites = [s for s in T.detect_all(ctx) if s.transform == "pipeline_insert"]
        assert sites, "no pipeline_insert site in aes_round"
        f.write_text(T.apply_site(sites[0], {"ROUNDS": 2}))

        _rename(RTL / "crypto" / "aes_round.sv", work / "gold.sv",
                {"aes_round": "gold_aes", "aes_sbox": "aes_sbox_g"})
        _rename(RTL / "crypto" / "aes_sbox.sv", work / "sbox_g.sv",
                {"aes_sbox": "aes_sbox_g"})
        _rename(f, work / "gate.sv",
                {"aes_round": "gate_aes", "aes_sbox": "aes_sbox_t"})
        _rename(gate_rtl / "crypto" / "aes_sbox.sv", work / "sbox_t.sv",
                {"aes_sbox": "aes_sbox_t"})

        exe = work / "sim"
        build = subprocess.run(
            ["iverilog", "-g2012",
             "-DGOLD=gold_aes", "-DGATE=gate_aes", "-DLATENCY=1",
             "-Paes_round.ROUNDS=2",
             "-o", str(exe), str(TB),
             str(work / "gold.sv"), str(work / "sbox_g.sv"),
             str(work / "gate.sv"), str(work / "sbox_t.sv")],
            capture_output=True, text=True)
        assert build.returncode == 0, build.stderr[-800:]

        run = subprocess.run([str(exe)], capture_output=True, text=True,
                             timeout=300)
        out = run.stdout
        assert "SIM PASS" in out, out[-1200:]
        m = re.search(r"checks=(\d+)", out)
        assert m and int(m.group(1)) > 50, f"too few comparisons: {out[-300:]}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
