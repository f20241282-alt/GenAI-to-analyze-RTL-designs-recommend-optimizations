"""OpenSTA driver: setup/hold analysis and machine-readable critical-path export."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import FlowConfig
from .tools import ToolResult, opensta


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PathPoint:
    pin: str
    arrival: float
    required: float
    slack: float

    @property
    def instance(self) -> str:
        return self.pin.rsplit("/", 1)[0] if "/" in self.pin else ""


@dataclass
class TimingPath:
    startpoint: str
    endpoint: str
    start_clock: str
    end_clock: str
    slack: float
    points: List[PathPoint] = field(default_factory=list)

    @property
    def depth(self) -> int:
        return len(self.points)

    @property
    def arrival(self) -> float:
        return self.points[-1].arrival if self.points else 0.0

    @property
    def required(self) -> float:
        return self.points[-1].required if self.points else 0.0

    def stage_delays(self) -> List[tuple[str, float]]:
        """(pin, incremental delay) for every point on the path."""
        out: List[tuple[str, float]] = []
        prev = None
        for p in self.points:
            d = 0.0 if prev is None else (p.arrival - prev)
            out.append((p.pin, d))
            prev = p.arrival
        return out

    def instances(self) -> List[str]:
        seen: List[str] = []
        for p in self.points:
            inst = p.instance
            if inst and inst not in seen:
                seen.append(inst)
        return seen

    def to_dict(self) -> dict:
        return {
            "startpoint": self.startpoint,
            "endpoint": self.endpoint,
            "start_clock": self.start_clock,
            "end_clock": self.end_clock,
            "slack": self.slack,
            "depth": self.depth,
            "arrival": self.arrival,
            "required": self.required,
            "points": [
                {"pin": p.pin, "arrival": p.arrival, "required": p.required,
                 "slack": p.slack} for p in self.points
            ],
        }


@dataclass
class StaResult:
    ok: bool
    wns: float = 0.0            # worst setup slack (ns); negative == violation
    tns: float = 0.0            # total negative setup slack (ns)
    whs: float = 0.0            # worst hold slack (ns)
    ths: float = 0.0            # total negative hold slack (ns)
    setup_violations: int = 0
    hold_violations: int = 0
    per_clock: Dict[str, dict] = field(default_factory=dict)
    setup_paths: List[TimingPath] = field(default_factory=list)
    hold_paths: List[TimingPath] = field(default_factory=list)
    clock_periods: Dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""
    log: str = ""

    def fmax_mhz(self) -> Dict[str, float]:
        """Achievable frequency per clock = 1 / (period - WNS_of_that_clock)."""
        out: Dict[str, float] = {}
        for clk, period in self.clock_periods.items():
            wns = self.per_clock.get(clk, {}).get("wns", 0.0)
            achievable = period - min(wns, 0.0)
            if achievable > 0:
                out[clk] = 1000.0 / achievable
        return out

    def summary(self) -> dict:
        return {
            "wns_ns": self.wns,
            "tns_ns": self.tns,
            "whs_ns": self.whs,
            "ths_ns": self.ths,
            "setup_violations": self.setup_violations,
            "hold_violations": self.hold_violations,
            "fmax_mhz": self.fmax_mhz(),
            "per_clock": self.per_clock,
        }


# ---------------------------------------------------------------------------
# Tcl generation
# ---------------------------------------------------------------------------
_TCL_HELPERS = r"""
proc json_escape {s} {
  set s [string map {\\ \\\\ \" \\\"} $s]
  return $s
}
proc jnum {v} {
  if {$v eq "" || $v eq "INF" || $v eq "-INF"} { return "null" }
  if {[string is double -strict $v]} { return $v }
  return "null"
}
proc dump_paths {fh label ends} {
  puts $fh "\"$label\": \["
  set first 1
  foreach end $ends {
    if {!$first} { puts $fh "," }
    set first 0
    set sp   [get_property $end startpoint]
    set ep   [get_property $end endpoint]
    set spn  [get_full_name $sp]
    set epn  [get_full_name $ep]
    set slk  [get_property $end slack]
    set sclk ""
    set eclk ""
    catch { set sclk [get_name [get_property $end startpoint_clock]] }
    catch { set eclk [get_name [get_property $end endpoint_clock]] }
    puts $fh "{"
    puts $fh "\"startpoint\": \"[json_escape $spn]\","
    puts $fh "\"endpoint\": \"[json_escape $epn]\","
    puts $fh "\"start_clock\": \"[json_escape $sclk]\","
    puts $fh "\"end_clock\": \"[json_escape $eclk]\","
    puts $fh "\"slack\": [jnum $slk],"
    puts $fh "\"points\": \["
    set pfirst 1
    foreach pt [get_property $end points] {
      if {!$pfirst} { puts $fh "," }
      set pfirst 0
      set pin [get_full_name [get_property $pt pin]]
      set arr [get_property $pt arrival]
      set req [get_property $pt required]
      set pslk [get_property $pt slack]
      puts $fh "{\"pin\": \"[json_escape $pin]\", \"arrival\": [jnum $arr], \"required\": [jnum $req], \"slack\": [jnum $pslk]}"
    }
    puts $fh "\]"
    puts $fh "}"
  }
  puts $fh "\]"
}
"""


def build_script(cfg: FlowConfig, netlist: Path, workdir: Path,
                 n_paths: int) -> str:
    plat = cfg.platform
    out_json = workdir / "sta.json"
    return f"""# auto-generated by genrtl.sta -- do not edit
{_TCL_HELPERS}

read_liberty -max "{plat.lib_max}"
read_liberty -min "{plat.lib_min}"
read_verilog "{netlist}"
link_design {cfg.top}
read_sdc "{cfg.sdc}"

set fh [open "{out_json}" w]
puts $fh "{{"

puts $fh "\\"wns\\": [jnum [worst_slack -max]],"
puts $fh "\\"tns\\": [jnum [total_negative_slack -max]],"
puts $fh "\\"whs\\": [jnum [worst_slack -min]],"
puts $fh "\\"ths\\": [jnum [total_negative_slack -min]],"

# ---- clock definitions --------------------------------------------------
puts $fh "\\"clocks\\": \\{{"
set cfirst 1
foreach clk [all_clocks] {{
  if {{!$cfirst}} {{ puts $fh "," }}
  set cfirst 0
  puts $fh "\\"[json_escape [get_name $clk]]\\": [jnum [get_property $clk period]]"
}}
puts $fh "\\}},"

# ---- worst setup paths ---------------------------------------------------
set setup_ends [find_timing_paths -path_delay max -group_path_count {n_paths} \\
                  -endpoint_path_count 1 -sort_by_slack -unique_paths_to_endpoint]
dump_paths $fh "setup_paths" $setup_ends
puts $fh ","

# ---- worst hold paths ----------------------------------------------------
set hold_ends [find_timing_paths -path_delay min -group_path_count {max(4, n_paths // 4)} \\
                  -endpoint_path_count 1 -sort_by_slack -unique_paths_to_endpoint]
dump_paths $fh "hold_paths" $hold_ends

puts $fh "\\}}"
close $fh

report_checks -path_delay max -group_path_count 5 -digits 4 > "{workdir / 'report_setup.txt'}"
report_checks -path_delay min -group_path_count 5 -digits 4 > "{workdir / 'report_hold.txt'}"
report_check_types -max_slew -max_capacitance -max_fanout -violators \\
    > "{workdir / 'report_check_types.txt'}"
"""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def _mk_paths(raw: List[dict]) -> List[TimingPath]:
    out: List[TimingPath] = []
    for r in raw:
        if r.get("slack") is None:
            continue
        pts = [
            PathPoint(p["pin"], p.get("arrival") or 0.0,
                      p.get("required") or 0.0, p.get("slack") or 0.0)
            for p in r.get("points", [])
        ]
        out.append(TimingPath(
            startpoint=r["startpoint"], endpoint=r["endpoint"],
            start_clock=r.get("start_clock", ""), end_clock=r.get("end_clock", ""),
            slack=float(r["slack"]), points=pts,
        ))
    out.sort(key=lambda p: p.slack)
    return out


def analyze(cfg: FlowConfig, netlist: Path, workdir: Path, *,
            name: str = "sta", n_paths: Optional[int] = None) -> StaResult:
    workdir.mkdir(parents=True, exist_ok=True)
    n = n_paths if n_paths is not None else cfg.n_paths
    script = build_script(cfg, netlist, workdir, n)
    res: ToolResult = opensta(script, workdir=workdir, name=name,
                              binary=cfg.tools.sta)

    out_json = workdir / "sta.json"
    if not out_json.is_file():
        return StaResult(ok=False, seconds=res.seconds,
                         error="OpenSTA produced no sta.json", log=res.tail(40))
    try:
        data = json.loads(out_json.read_text())
    except json.JSONDecodeError as exc:
        return StaResult(ok=False, seconds=res.seconds,
                         error=f"malformed sta.json: {exc}", log=res.tail(40))

    setup_paths = _mk_paths(data.get("setup_paths") or [])
    hold_paths = _mk_paths(data.get("hold_paths") or [])

    per_clock: Dict[str, dict] = {}
    for p in setup_paths:
        c = p.end_clock or "unclocked"
        rec = per_clock.setdefault(
            c, {"wns": float("inf"), "tns": 0.0, "violations": 0})
        rec["wns"] = min(rec["wns"], p.slack)
        if p.slack < 0:
            rec["tns"] += p.slack
            rec["violations"] += 1
    for p in hold_paths:
        c = p.end_clock or "unclocked"
        rec = per_clock.setdefault(
            c, {"wns": float("inf"), "tns": 0.0, "violations": 0})
        rec["whs"] = min(rec.get("whs", float("inf")), p.slack)
    for rec in per_clock.values():
        if rec["wns"] == float("inf"):
            rec["wns"] = 0.0
        if rec.get("whs") == float("inf"):
            rec["whs"] = 0.0

    return StaResult(
        ok=True,
        wns=float(data.get("wns") or 0.0),
        tns=float(data.get("tns") or 0.0),
        whs=float(data.get("whs") or 0.0),
        ths=float(data.get("ths") or 0.0),
        setup_violations=sum(1 for p in setup_paths if p.slack < 0),
        hold_violations=sum(1 for p in hold_paths if p.slack < 0),
        per_clock=per_clock,
        setup_paths=setup_paths,
        hold_paths=hold_paths,
        clock_periods={k: float(v) for k, v in (data.get("clocks") or {}).items()
                       if v is not None},
        seconds=res.seconds,
        log=res.tail(10),
    )
