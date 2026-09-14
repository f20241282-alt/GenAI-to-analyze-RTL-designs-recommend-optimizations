"""CDC / clock-generation protection: the optimiser's do-not-touch list.

Four independent detectors run, and a module is protected if *any* of them
fires. Belt and braces is deliberate: silently retiming one flop of a two-flop
synchroniser produces a design that still passes equivalence checking on the
combinational function while destroying the MTBF property that made the
crossing legal in the first place.

    1. path      -- the file lives under a protected directory (rtl/cdc, rtl/clk)
    2. name      -- the module name matches a known CDC/clock naming pattern
    3. annotation-- the RTL carries `genrtl-protect:` or (* genrtl_protect *)
    4. structural-- an unannotated N-flop synchroniser chain is found in the
                    elaborated netlist

Detector 4 is what protects CDC structures somebody forgot to label.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .netlist import Netlist, base_module_name, parse_src

PROTECTED_DIRS = ("rtl/cdc", "rtl/clk")

PROTECTED_NAME_PATTERNS = [
    r"^cdc_",
    r"_cdc$",
    r"sync_\d*ff$",
    r"_sync$",
    r"_synchron",
    r"async_fifo",
    r"_fifo$",
    r"pulse_sync",
    r"handshake",
    r"clkgen",
    r"clk_gen",
    r"clk_div",
    r"_reset_sync",
]

_ANNOT_RE = re.compile(r"genrtl[-_]protect", re.IGNORECASE)

_FF_TYPES = ("$dff", "$adff", "$sdff", "$dffe", "$adffe", "$_DFF_", "$_SDFF_",
             "DFF", "SDFF", "DFFR", "DFFS")


@dataclass
class ProtectionRecord:
    module: str
    reasons: List[str] = field(default_factory=list)
    file: Optional[Path] = None

    def as_dict(self) -> dict:
        return {"module": self.module, "reasons": self.reasons,
                "file": str(self.file) if self.file else None}


@dataclass
class ProtectionSet:
    modules: Dict[str, ProtectionRecord] = field(default_factory=dict)
    files: Set[Path] = field(default_factory=set)

    def is_module_protected(self, module: str) -> bool:
        return base_module_name(module) in self.modules

    def is_file_protected(self, path: Path) -> bool:
        p = Path(path).resolve()
        if p in self.files:
            return True
        s = str(p).replace("\\", "/")
        return any(f"/{d}/" in s for d in PROTECTED_DIRS)

    def reasons_for(self, module: str) -> List[str]:
        rec = self.modules.get(base_module_name(module))
        return rec.reasons if rec else []

    def summary(self) -> List[dict]:
        return [r.as_dict() for r in sorted(self.modules.values(),
                                            key=lambda r: r.module)]


def _add(ps: ProtectionSet, module: str, reason: str,
         file: Optional[Path] = None) -> None:
    name = base_module_name(module)
    rec = ps.modules.setdefault(name, ProtectionRecord(module=name, file=file))
    if reason not in rec.reasons:
        rec.reasons.append(reason)
    if file is not None:
        rec.file = file
        ps.files.add(Path(file).resolve())


def _detect_structural_synchronizers(nl: Netlist, ps: ProtectionSet) -> None:
    """Flag modules that contain an unbroken chain of >=2 flops with no logic.

    The elaborated netlist is used, where registers are still ``$dff``-family
    cells and their connectivity is intact. A chain counts as a synchroniser
    when all of the following hold:

      * flop B's D input is driven directly by flop A's Q output,
      * the head of the chain takes its D straight from a module input,
      * every flop in the chain is enable-free, and
      * each intermediate flop's Q feeds *only* the next flop in the chain.

    The last two conditions are what separate a synchroniser from a pipeline
    register. A valid-delay flop chain running from a module input to a module
    output is structurally identical to a two-flop synchroniser -- until you
    notice that the intermediate flop also gates the datapath, which a real
    synchroniser's never does. Without that check, pipelining a MAC would make
    the optimiser refuse to touch it again.
    """
    for mod_name, mod in nl.modules.items():
        cells = mod.get("cells", {})
        ports = mod.get("ports", {})
        input_bits: Set[int] = set()
        output_bits: Set[int] = set()
        for pname, p in ports.items():
            bits = {b for b in p.get("bits", []) if isinstance(b, int)}
            if p.get("direction") in ("input", "inout"):
                input_bits |= bits
            if p.get("direction") in ("output", "inout"):
                output_bits |= bits

        # bit -> driving flop, flop -> its D bits / Q bits, and bit -> readers
        driver: Dict[int, str] = {}
        d_bits: Dict[str, List[int]] = {}
        q_bits: Dict[str, List[int]] = {}
        has_enable: Dict[str, bool] = {}
        readers: Dict[int, Set[str]] = {}

        for cname, c in cells.items():
            conns = c.get("connections", {})
            ctype = c.get("type", "")
            is_ff = any(ctype.startswith(t) or t in ctype for t in _FF_TYPES)
            for port, bits in conns.items():
                if port.strip("\\") in ("Q", "QN"):
                    continue
                for b in bits:
                    if isinstance(b, int):
                        readers.setdefault(b, set()).add(cname)
            if not is_ff:
                continue
            q = conns.get("Q") or conns.get("\\Q") or []
            d = conns.get("D") or conns.get("\\D") or []
            d_bits[cname] = [b for b in d if isinstance(b, int)]
            q_bits[cname] = [b for b in q if isinstance(b, int)]
            has_enable[cname] = any(
                p.strip("\\") in ("EN", "E") for p in conns) or "e" in ctype.lower().replace("dff", "")
            for b in q_bits[cname]:
                driver[b] = cname

        chain_len = 0
        for cname in d_bits:
            seen: Set[str] = set()
            n = 1
            cur = cname
            pure = True
            while True:
                srcs = {driver[b] for b in d_bits.get(cur, []) if b in driver}
                if len(srcs) != 1:
                    break
                nxt = srcs.pop()
                if nxt in seen or nxt == cur:
                    break
                # the upstream flop must feed nothing but this one
                fanout = set()
                for b in q_bits.get(nxt, []):
                    fanout |= readers.get(b, set())
                    if b in output_bits:
                        fanout.add("<module output>")
                if fanout - {cur}:
                    pure = False
                    break
                if has_enable.get(nxt) or has_enable.get(cur):
                    pure = False
                    break
                seen.add(nxt)
                n += 1
                cur = nxt
            head_from_port = any(b in input_bits for b in d_bits.get(cur, []))
            if pure and n >= 2 and head_from_port:
                chain_len = max(chain_len, n)

        if chain_len >= 2:
            _add(ps, mod_name,
                 f"structural: {chain_len}-flop synchroniser chain fed from a module input",
                 parse_src(mod.get("attributes", {}).get("src")).file
                 if parse_src(mod.get("attributes", {}).get("src")) else None)


def build(nl_elab: Netlist, rtl_root: Path,
          extra_modules: Optional[List[str]] = None) -> ProtectionSet:
    """Compute the protected-module set for a design."""
    ps = ProtectionSet()

    # ---- 1 & 2 & 3: path, name and annotation ----------------------------
    for mod_name in nl_elab.modules:
        base = base_module_name(mod_name)
        src = nl_elab.module_src(mod_name)
        f = src.file if src else None

        if f is not None:
            s = str(f).replace("\\", "/")
            if any(f"/{d}/" in s for d in PROTECTED_DIRS):
                _add(ps, base, f"path: file lives under {[d for d in PROTECTED_DIRS if f'/{d}/' in s][0]}", f)

        for pat in PROTECTED_NAME_PATTERNS:
            if re.search(pat, base):
                _add(ps, base, f"name: matches /{pat}/", f)
                break

        if f is not None and Path(f).is_file():
            head = "\n".join(Path(f).read_text(errors="replace").splitlines()[:60])
            if _ANNOT_RE.search(head):
                _add(ps, base, "annotation: genrtl-protect marker in file header", f)

    # ---- 4: structural synchroniser detection ----------------------------
    _detect_structural_synchronizers(nl_elab, ps)

    for m in (extra_modules or []):
        _add(ps, m, "user: listed on the command line")

    return ps


def check_patch_targets(ps: ProtectionSet, files: List[Path]) -> List[str]:
    """Return a violation message per protected file touched by a patch."""
    out: List[str] = []
    for f in files:
        if ps.is_file_protected(f):
            out.append(f"patch touches protected file {f}")
    return out
