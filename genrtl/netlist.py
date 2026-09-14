"""Yosys JSON netlist model: hierarchy walking and source-annotation lookup.

Yosys records a ``src`` attribute of the form ``file:line.col-line.col`` on
modules and on most cells. Technology mapping (ABC) destroys the attribute on
combinational cells but *keeps* it on registers, and module-level annotations
always survive. Both facts are used below.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SRC_RE = re.compile(r"^(?P<file>.+?):(?P<l0>\d+)(?:\.\d+)?(?:-(?P<l1>\d+)(?:\.\d+)?)?$")
#  $paramod\rv_lane\LANE_ID=s32'000...   /  $paramod$<hash>\cdc_sync_2ff
_PARAMOD_RE = re.compile(r"^\$paramod(?:\$[0-9a-f]+)?\\(?P<name>[A-Za-z_][\w$]*)")


@dataclass(frozen=True)
class SrcRef:
    file: Path
    line_start: int
    line_end: int

    @property
    def span(self) -> int:
        return self.line_end - self.line_start + 1


def parse_src(value: Optional[str]) -> Optional[SrcRef]:
    """Parse a Yosys ``src`` attribute. Multi-entry values take the first."""
    if not value:
        return None
    first = value.split("|")[0].strip()
    m = _SRC_RE.match(first)
    if not m:
        return None
    l0 = int(m.group("l0"))
    l1 = int(m.group("l1") or l0)
    return SrcRef(Path(m.group("file")), min(l0, l1), max(l0, l1))


def base_module_name(name: str) -> str:
    """Strip Yosys ``$paramod`` decoration to recover the RTL module name."""
    m = _PARAMOD_RE.match(name)
    if m:
        return m.group("name")
    return name.lstrip("\\")


@dataclass
class ResolvedObject:
    """Where a netlist pin lives in the design hierarchy."""

    pin: str
    #: instance path of the containing module instance ("" for top)
    instance_path: str
    #: decorated Yosys module name of the containing module
    module: str
    #: RTL module name
    module_base: str
    #: leaf cell name inside that module ("" if the pin is a module port)
    leaf_cell: str
    leaf_type: str = ""
    #: src of the leaf cell, when technology mapping preserved it
    leaf_src: Optional[SrcRef] = None
    #: src span of the containing module (always available)
    module_src: Optional[SrcRef] = None


class Netlist:
    def __init__(self, data: dict, top: str):
        self.data = data
        self.top = top
        self.modules: Dict[str, dict] = data["modules"]
        if top not in self.modules:
            raise KeyError(f"top module {top!r} not in netlist JSON")

    # -- construction --------------------------------------------------------
    @classmethod
    def load(cls, path: Path, top: str) -> "Netlist":
        with Path(path).open() as fh:
            return cls(json.load(fh), top)

    # -- basic lookups -------------------------------------------------------
    def module_src(self, module: str) -> Optional[SrcRef]:
        mod = self.modules.get(module)
        if not mod:
            return None
        return parse_src(mod.get("attributes", {}).get("src"))

    def cell(self, module: str, cell: str) -> Optional[dict]:
        mod = self.modules.get(module)
        if not mod:
            return None
        return mod.get("cells", {}).get(cell)

    def submodule_of(self, module: str, inst: str) -> Optional[str]:
        c = self.cell(module, inst)
        if not c:
            return None
        t = c["type"]
        return t if t in self.modules else None

    # -- hierarchy walking ---------------------------------------------------
    def resolve_pin(self, pin_path: str) -> ResolvedObject:
        """Resolve an OpenSTA hierarchical pin name to its owning RTL module.

        ``g_rv[0].u_lane/u_alu/_3565_/D`` ->
            instance_path = "g_rv[0].u_lane/u_alu"
            module_base   = "rv_alu"
            leaf_cell     = "_3565_"
        """
        parts = pin_path.split("/")
        module = self.top
        inst_parts: List[str] = []

        # Walk while each part names a submodule instance in the current module.
        i = 0
        while i < len(parts) - 1:
            sub = self.submodule_of(module, parts[i])
            if sub is None:
                break
            inst_parts.append(parts[i])
            module = sub
            i += 1

        leaf_cell = parts[i] if i < len(parts) - 1 else ""
        leaf = self.cell(module, leaf_cell) if leaf_cell else None
        leaf_src = parse_src((leaf or {}).get("attributes", {}).get("src"))

        return ResolvedObject(
            pin=pin_path,
            instance_path="/".join(inst_parts),
            module=module,
            module_base=base_module_name(module),
            leaf_cell=leaf_cell,
            leaf_type=(leaf or {}).get("type", ""),
            leaf_src=leaf_src,
            module_src=self.module_src(module),
        )

    # -- aggregate queries ---------------------------------------------------
    def module_of_instance_path(self, inst_path: str) -> Optional[str]:
        module = self.top
        if not inst_path:
            return module
        for part in inst_path.split("/"):
            sub = self.submodule_of(module, part)
            if sub is None:
                return None
            module = sub
        return module

    def rtl_modules(self) -> Dict[str, SrcRef]:
        """RTL module name -> source reference (first parameterisation wins)."""
        out: Dict[str, SrcRef] = {}
        for name in self.modules:
            src = self.module_src(name)
            if src is None:
                continue
            out.setdefault(base_module_name(name), src)
        return out

    def instances_of_module(self, base_name: str) -> List[str]:
        """All decorated module names whose RTL name is ``base_name``."""
        return [n for n in self.modules if base_module_name(n) == base_name]


@lru_cache(maxsize=64)
def load_netlist(path: str, top: str) -> Netlist:
    return Netlist.load(Path(path), top)
