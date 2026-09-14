"""Static configuration: platforms, benchmark sizes, tool paths, run layout."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
RTL_DIR = REPO / "rtl"
FLOW_DIR = REPO / "flow"
CONSTRAINTS_DIR = REPO / "constraints"
RUNS_DIR = REPO / "runs"


# ---------------------------------------------------------------------------
# Standard-cell platforms
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Platform:
    """A standard-cell library set.

    ``lib_max`` is the slow corner used for setup analysis, ``lib_min`` the fast
    corner used for hold analysis and ``lib_map`` the corner handed to Yosys for
    technology mapping.
    """

    name: str
    lib_map: Path
    lib_max: Path
    lib_min: Path
    buf_cell: str
    buf_pin: str
    #: ABC target delay in picoseconds (tightest clock period in the SDC).
    abc_delay_ps: int = 1300

    @property
    def available(self) -> bool:
        return self.lib_map.is_file() and self.lib_map.stat().st_size > 1024

    @property
    def corners_degraded(self) -> bool:
        """True when min/max corners fall back to the mapping corner."""
        return self.lib_max == self.lib_map or self.lib_min == self.lib_map


def _pdk_root() -> Path:
    return Path(os.environ.get("GENRTL_PDK_ROOT", "/opt/pdk"))


def _sky_lib(name: str) -> Path:
    p = _pdk_root() / "sky130hd" / name
    tt = _pdk_root() / "sky130hd" / "sky130_fd_sc_hd__tt_025C_1v80.lib"
    return p if (p.is_file() and p.stat().st_size > 1024) else tt


PLATFORMS: Dict[str, Platform] = {
    "nangate45": Platform(
        name="nangate45",
        lib_map=_pdk_root() / "nangate45" / "Nangate45_typ.lib",
        lib_max=_pdk_root() / "nangate45" / "Nangate45_slow.lib",
        lib_min=_pdk_root() / "nangate45" / "Nangate45_fast.lib",
        buf_cell="BUF_X4",
        buf_pin="Z",
    ),
    "sky130hd": Platform(
        name="sky130hd",
        lib_map=_sky_lib("sky130_fd_sc_hd__tt_025C_1v80.lib"),
        lib_max=_sky_lib("sky130_fd_sc_hd__ss_n40C_1v40.lib"),
        lib_min=_sky_lib("sky130_fd_sc_hd__ff_n40C_1v95.lib"),
        buf_cell="sky130_fd_sc_hd__buf_4",
        buf_pin="X",
        abc_delay_ps=4000,
    ),
}


# ---------------------------------------------------------------------------
# Benchmark size configurations
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BenchConfig:
    name: str
    defines: List[str]
    description: str


BENCHES: Dict[str, BenchConfig] = {
    "small": BenchConfig(
        name="small",
        defines=["NEBULA_CFG_SMALL"],
        description="Fast-iteration configuration (~21 K cells): 1 lane per engine, AES 1 round.",
    ),
    "full": BenchConfig(
        name="full",
        defines=["NEBULA_CFG_FULL"],
        description="~50 K standard-cell benchmark: 4 DSP / 3 RV / 3 CRC lanes, AES 2 rounds.",
    ),
}


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Toolchain:
    yosys: str
    sta: str
    verilator: Optional[str]
    iverilog: Optional[str]
    eqy: Optional[str]
    sby: Optional[str]

    def require(self) -> None:
        missing = [n for n in ("yosys", "sta") if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                f"Required tool(s) not found on PATH: {', '.join(missing)}. "
                "Run setup/install_tools.sh first."
            )


def discover_tools() -> Toolchain:
    def which(name: str, env: str) -> Optional[str]:
        return os.environ.get(env) or shutil.which(name)

    return Toolchain(
        yosys=which("yosys", "GENRTL_YOSYS") or "",
        sta=which("sta", "GENRTL_STA") or "",
        verilator=which("verilator", "GENRTL_VERILATOR"),
        iverilog=which("iverilog", "GENRTL_IVERILOG"),
        eqy=which("eqy", "GENRTL_EQY"),
        sby=which("sby", "GENRTL_SBY"),
    )


# ---------------------------------------------------------------------------
# Flow configuration
# ---------------------------------------------------------------------------
@dataclass
class FlowConfig:
    platform: Platform
    bench: BenchConfig
    top: str = "nebula_top"
    sdc: Path = CONSTRAINTS_DIR / "nebula.sdc"
    filelist: Path = FLOW_DIR / "filelist.f"
    #: number of worst setup paths pulled out of STA each iteration
    n_paths: int = 20
    #: maximum closed-loop iterations
    max_iters: int = 12
    #: how many distinct transforms may be tried on one path before moving on
    max_attempts_per_path: int = 3
    #: minimum WNS improvement (ns) for a patch to count as an improvement
    min_wns_gain_ns: float = 0.005
    #: reject a patch that grows total cell area by more than this fraction
    max_area_growth: float = 0.10
    #: hold slack must stay above this (ns)
    hold_floor_ns: float = 0.0
    #: BMC depth for sequential (latency-changing) equivalence proofs
    seq_bmc_depth: int = 24
    tools: Toolchain = field(default_factory=discover_tools)

    def rtl_files(self) -> List[Path]:
        out: List[Path] = []
        for raw in self.filelist.read_text().splitlines():
            line = raw.split("//")[0].strip()
            if line:
                out.append(REPO / line)
        return out
