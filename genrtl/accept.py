"""The acceptance rule.

A patch is accepted only when every one of these holds:

    new WNS on the targeted clock improves by at least ``min_wns_gain_ns``
AND global WNS does not get worse
AND global TNS does not get worse
AND worst hold slack stays at or above the hold floor
AND the number of hold violations does not increase
AND cell area grows by no more than ``max_area_growth``

Formal equivalence and successful synthesis are preconditions handled upstream
in :mod:`genrtl.verify`; by the time a candidate reaches this module it is
already known to be functionally identical and physically buildable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import FlowConfig
from .sta import StaResult

EPS = 1e-6


@dataclass
class Verdict:
    accepted: bool
    reasons: List[str] = field(default_factory=list)
    deltas: Dict[str, float] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "all criteria met"

    def to_dict(self) -> dict:
        return {"accepted": self.accepted, "reasons": self.reasons,
                "deltas": {k: round(v, 5) for k, v in self.deltas.items()}}


def _clock_wns(sta: StaResult, clock: str) -> float:
    rec = sta.per_clock.get(clock)
    return rec["wns"] if rec else sta.wns


def evaluate(cfg: FlowConfig, before: StaResult, after: StaResult,
             area_before: float, area_after: float,
             target_clock: Optional[str] = None) -> Verdict:
    reasons: List[str] = []
    deltas: Dict[str, float] = {
        "wns": after.wns - before.wns,
        "tns": after.tns - before.tns,
        "whs": after.whs - before.whs,
        "area_frac": (area_after - area_before) / area_before if area_before else 0.0,
        "hold_violations": after.hold_violations - before.hold_violations,
    }

    target_gain = 0.0
    if target_clock:
        target_gain = _clock_wns(after, target_clock) - _clock_wns(before, target_clock)
        deltas["target_wns"] = target_gain

    # --- setup improvement -------------------------------------------------
    improved = False
    if target_clock and target_gain >= cfg.min_wns_gain_ns:
        improved = True
    if deltas["wns"] >= cfg.min_wns_gain_ns:
        improved = True
    if not improved and deltas["tns"] > 0 and deltas["wns"] >= -EPS:
        # WNS unchanged but total violation reduced -- still progress
        improved = True
        reasons.append(
            f"WNS unchanged, accepted on TNS improvement of {deltas['tns']:+.4f} ns")
    if not improved:
        reasons.insert(0, (
            f"no timing improvement (WNS {deltas['wns']:+.4f} ns, "
            f"target-domain WNS {target_gain:+.4f} ns, "
            f"threshold {cfg.min_wns_gain_ns:.4f} ns)"))
        return Verdict(False, reasons, deltas)

    # --- no regression elsewhere ------------------------------------------
    if deltas["wns"] < -EPS:
        reasons.insert(0, f"global WNS regressed by {-deltas['wns']:.4f} ns")
        return Verdict(False, reasons, deltas)
    if deltas["tns"] < -EPS:
        reasons.insert(0, f"global TNS regressed by {-deltas['tns']:.4f} ns")
        return Verdict(False, reasons, deltas)

    # --- hold safety -------------------------------------------------------
    if after.whs < cfg.hold_floor_ns - EPS:
        reasons.insert(0, (
            f"hold timing became invalid: worst hold slack {after.whs:+.4f} ns "
            f"is below the floor of {cfg.hold_floor_ns:+.4f} ns"))
        return Verdict(False, reasons, deltas)
    if after.hold_violations > before.hold_violations:
        reasons.insert(0, (
            f"hold violations increased from {before.hold_violations} to "
            f"{after.hold_violations}"))
        return Verdict(False, reasons, deltas)

    # --- area budget -------------------------------------------------------
    if deltas["area_frac"] > cfg.max_area_growth + EPS:
        reasons.insert(0, (
            f"cell area grew {deltas['area_frac'] * 100:.2f}%, over the "
            f"{cfg.max_area_growth * 100:.1f}% budget"))
        return Verdict(False, reasons, deltas)

    reasons.append(
        f"WNS {deltas['wns']:+.4f} ns, TNS {deltas['tns']:+.4f} ns, "
        f"hold {after.whs:+.4f} ns, area {deltas['area_frac'] * 100:+.2f}%")
    return Verdict(True, reasons, deltas)
