"""The GenAI optimisation engine.

The model's job is narrow on purpose: given one localised failing path and the
catalogue of safe transformations, pick the transformation and its parameters.
The rewrite itself is performed by :mod:`genrtl.transforms`, and every proposal
still has to clear the verification gate. Narrowing the model's output to a
choice plus parameters is what removes the whole class of "the LLM wrote
plausible RTL that silently broke the design" failures.

Backends
--------
``anthropic`` / ``openai`` / ``ollama``  real LLM, selected by ``GENRTL_LLM``
``heuristic``                           deterministic rule-based proposer

The heuristic backend is not a stub: it ranks the same candidate sites using
the same path evidence, so the closed loop runs end to end with no API key and
the LLM can be dropped in later without touching anything else.
"""
from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .localize import Localization
from .protect import ProtectionSet
from .sta import StaResult
from . import transforms as T


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------
@dataclass
class Proposal:
    site: Optional[T.TransformSite]
    explanation: str
    backend: str
    raw: str = ""
    error: str = ""
    considered: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.site is not None

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "transform": self.site.transform if self.site else None,
            "module": self.site.module if self.site else None,
            "params": self.site.params if self.site else None,
            "explanation": self.explanation,
            "considered": self.considered,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are an RTL timing-closure assistant working inside a closed-loop optimiser.

You are given ONE failing timing path, the small slice of RTL that produced it,
and a fixed catalogue of safe transformations. Your job is to choose exactly one
transformation from the catalogue and supply its parameters. You never write RTL
directly: a verified rewriter applies the transformation for you.

Every proposal is then checked by synthesis, formal equivalence and static
timing analysis. A proposal that does not improve setup slack, or that fails
equivalence, is discarded. So propose the change with the best chance of
reducing the logic depth of THIS path, and do not propose changes to modules
marked PROTECTED under any circumstances.

Reply with a single JSON object and nothing else:

  {"transform": "<name from the catalogue>",
   "params": { ... },
   "reason": "<one or two sentences on why this shortens this path>"}

If no catalogued transformation applies to this path, reply
  {"transform": null, "params": {}, "reason": "<why>"}
"""


def build_prompt(loc: Localization, sta: StaResult, protection: ProtectionSet,
                 sites: List[T.TransformSite], sdc_text: str,
                 iteration: int, history: List[dict]) -> str:
    p = loc.path
    period = sta.clock_periods.get(p.end_clock, 0.0)

    stages = p.stage_delays()
    top_stages = sorted(stages, key=lambda s: -s[1])[:6]
    stage_txt = "\n".join(
        f"    {d * 1000:8.1f} ps   {pin}" for pin, d in top_stages)

    prot_txt = "\n".join(
        f"    {r['module']:22s} {'; '.join(r['reasons'][:2])}"
        for r in protection.summary())

    cat = []
    for t in T.catalogue():
        cat.append(
            f"  - {t['name']} ({t['kind']}, proof: {t['equivalence']})\n"
            f"      {' '.join(t['description'].split())}\n"
            f"      params: {json.dumps(t['params'].get('properties', {}))}")
    cat_txt = "\n".join(cat)

    site_txt = "\n".join(
        f"  - {s.transform} on module {s.module} (line {s.line})\n"
        f"      params: {json.dumps(s.params)}\n"
        f"      why it matches: {' '.join(s.rationale.split())}"
        for s in sites[:10]) or "  (the pattern matcher found no applicable site)"

    hist_txt = "\n".join(
        f"  iteration {h['iteration']}: {h['transform']} on {h['module']} -> "
        f"{h['verdict']} ({h['reason']})" for h in history[-6:]) or "  (none yet)"

    return textwrap.dedent(f"""\
        ## Optimisation objective
        Close setup timing on clock {p.end_clock} (period {period:.3f} ns)
        without breaking hold timing, without changing function, and without
        touching any protected CDC or clock-generation logic.

        ## Failing path (iteration {iteration})
        startpoint : {p.startpoint}
        endpoint   : {p.endpoint}
        launch clk : {p.start_clock}
        capture clk: {p.end_clock}  (period {period:.3f} ns)
        slack      : {p.slack:+.4f} ns   ({'VIOLATED' if p.slack < 0 else 'met'})
        arrival    : {p.arrival:.4f} ns
        required   : {p.required:.4f} ns
        logic depth: {p.depth} timing points
        diagnosis  : {loc.bottleneck}

        Delay attributed by RTL module (ns):
        {json.dumps({k: round(v, 4) for k, v in sorted(loc.delay_by_module.items(), key=lambda x: -x[1])}, indent=4)}

        Heaviest stages on the path:
        {stage_txt}

        ## Localised RTL
        module : {loc.primary.module_base if loc.primary else '?'}
        file   : {loc.slice_file}
        lines  : {loc.slice_start}-{loc.slice_end}

        ```systemverilog
        {loc.numbered_slice()}
        ```

        ## PROTECTED modules -- never propose a change inside these
        {prot_txt}

        ## Transformation catalogue
        {cat_txt}

        ## Sites the structural matcher already found on this path
        {site_txt}

        ## What has been tried so far
        {hist_txt}

        ## Relevant constraints
        ```
        {sdc_text}
        ```

        Choose one transformation and give its parameters as JSON.
        """)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class Backend:
    name = "base"

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class AnthropicBackend(Backend):
    name = "anthropic"

    def __init__(self, model: Optional[str] = None):
        import anthropic  # noqa: F401  (import error surfaces at construction)
        self._anthropic = anthropic
        self.model = model or os.environ.get(
            "GENRTL_LLM_MODEL", "claude-sonnet-4-5")
        self.client = anthropic.Anthropic()

    def complete(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text")


class OpenAIBackend(Backend):
    name = "openai"

    def __init__(self, model: Optional[str] = None):
        from openai import OpenAI
        self.model = model or os.environ.get("GENRTL_LLM_MODEL", "gpt-4o-mini")
        self.client = OpenAI()

    def complete(self, system: str, user: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model, max_tokens=1024,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return r.choices[0].message.content or ""


class OllamaBackend(Backend):
    name = "ollama"

    def __init__(self, model: Optional[str] = None):
        import urllib.request  # stdlib only
        self._url = os.environ.get("GENRTL_OLLAMA_URL",
                                   "http://localhost:11434/api/chat")
        self.model = model or os.environ.get("GENRTL_LLM_MODEL", "qwen2.5-coder")
        self._urllib = urllib.request

    def complete(self, system: str, user: str) -> str:
        payload = json.dumps({
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}).encode()
        req = self._urllib.Request(self._url, data=payload,
                                   headers={"Content-Type": "application/json"})
        with self._urllib.urlopen(req, timeout=180) as fh:
            return json.loads(fh.read()).get("message", {}).get("content", "")


def make_backend(name: Optional[str] = None) -> Optional[Backend]:
    """Instantiate the configured LLM backend, or None to use the heuristic."""
    name = (name or os.environ.get("GENRTL_LLM", "heuristic")).lower()
    try:
        if name == "anthropic":
            return AnthropicBackend()
        if name == "openai":
            return OpenAIBackend()
        if name == "ollama":
            return OllamaBackend()
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_response(text: str) -> Tuple[Optional[str], Dict, str]:
    m = _JSON_RE.search(text or "")
    if not m:
        return None, {}, "no JSON object in the model reply"
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return None, {}, f"malformed JSON in the model reply: {exc}"
    name = obj.get("transform")
    params = obj.get("params") or {}
    reason = obj.get("reason") or ""
    if name is None:
        return None, {}, reason or "model declined to propose a transformation"
    if not isinstance(params, dict):
        return None, {}, "params must be an object"
    return str(name), params, reason


# ---------------------------------------------------------------------------
# The proposer
# ---------------------------------------------------------------------------
#: How well each transform suits each STA diagnosis. A weight of 0 means the
#: transform does not address that failure mode at all and is not offered --
#: duplicating a register does nothing for a path that is simply too deep, and
#: rebalancing a tree does nothing for a path dominated by one slow driver.
_BOTTLENECK_BONUS = {
    "logic_depth": {"balanced_adder_tree": 1.6, "balanced_mux_tree": 1.6,
                    "logic_restructure": 1.4, "pipeline_insert": 1.5,
                    "boolean_factor": 1.1, "fsm_reencode": 1.0,
                    "register_retime": 1.1, "resource_duplication": 0.0},
    "moderate_depth": {"balanced_mux_tree": 1.3, "balanced_adder_tree": 1.3,
                       "logic_restructure": 1.2, "boolean_factor": 1.2,
                       "fsm_reencode": 1.2, "pipeline_insert": 1.2,
                       "register_retime": 1.0, "resource_duplication": 0.0},
    "single_dominant_stage": {"resource_duplication": 1.6, "fsm_reencode": 1.4,
                              "boolean_factor": 1.3, "logic_restructure": 1.1,
                              "balanced_mux_tree": 1.0, "balanced_adder_tree": 1.0,
                              "pipeline_insert": 0.9, "register_retime": 0.9},
    "load_or_fanout": {"resource_duplication": 1.8, "boolean_factor": 1.1,
                       "fsm_reencode": 1.0, "logic_restructure": 0.9,
                       "pipeline_insert": 0.0, "balanced_adder_tree": 0.0,
                       "balanced_mux_tree": 0.0, "register_retime": 0.0},
}


def candidate_sites(loc: Localization,
                    tried: Optional[set] = None,
                    module_params: Optional[Dict[str, Dict[str, int]]] = None
                    ) -> List[T.TransformSite]:
    """Every catalogued transformation that structurally matches this path.

    ``module_params`` carries the parameter values each module is actually
    instantiated with, so a transform can refuse a site that only exists at a
    parameterisation the design does not use.
    """
    tried = tried or set()
    module_params = module_params or {}
    sites: List[T.TransformSite] = []
    seen: set = set()
    for hit in (loc.candidates or ([loc.primary] if loc.primary else [])):
        if hit is None or hit.file is None or hit.protected:
            continue
        ctx = T.TransformContext.build(hit.file, hit.module_base,
                                       module_params.get(hit.module_base))
        if ctx is None:
            continue
        for s in T.detect_all(ctx):
            k = s.key()
            if k in seen or k in tried:
                continue
            seen.add(k)
            sites.append(s)
    bonus = _BOTTLENECK_BONUS.get(loc.bottleneck, {})
    sites = [s for s in sites if bonus.get(s.transform, 1.0) > 0.0]
    sites.sort(key=lambda s: -(s.est_gain * bonus.get(s.transform, 1.0)))
    return sites


def propose(loc: Localization, sta: StaResult, protection: ProtectionSet,
            sdc_text: str, iteration: int, history: List[dict],
            backend: Optional[Backend] = None,
            tried: Optional[set] = None,
            module_params: Optional[Dict[str, Dict[str, int]]] = None
            ) -> Proposal:
    """Pick one transformation for this path."""
    sites = candidate_sites(loc, tried, module_params)
    considered = [s.key() for s in sites]

    if not sites:
        return Proposal(None, "no catalogued transformation matches this path",
                        backend.name if backend else "heuristic",
                        considered=considered)

    if backend is None:
        best = sites[0]
        return Proposal(
            best,
            f"[heuristic] {best.rationale} Diagnosis '{loc.bottleneck}' favours "
            f"{best.transform}.",
            "heuristic", considered=considered)

    user = build_prompt(loc, sta, protection, sites, sdc_text, iteration, history)
    try:
        raw = backend.complete(SYSTEM_PROMPT, user)
    except Exception as exc:
        best = sites[0]
        return Proposal(best,
                        f"[heuristic fallback after LLM error] {best.rationale}",
                        "heuristic", error=f"{backend.name} call failed: {exc}",
                        considered=considered)

    name, params, reason = parse_response(raw)
    if name is None:
        return Proposal(None, reason, backend.name, raw=raw,
                        error="model proposed nothing", considered=considered)
    if name not in T.REGISTRY:
        return Proposal(None, reason, backend.name, raw=raw,
                        error=f"unknown transform {name!r}", considered=considered)

    # Bind the model's choice to a concrete detected site so its parameters are
    # validated against the RTL rather than trusted.
    chosen = None
    for s in sites:
        if s.transform != name:
            continue
        if all(str(s.params.get(k)) == str(v) for k, v in params.items()
               if k in s.params):
            chosen = s
            break
    if chosen is None:
        same = [s for s in sites if s.transform == name]
        if not same:
            return Proposal(
                None, reason, backend.name, raw=raw,
                error=(f"{name} does not match any site on this path; the "
                       f"structural precondition does not hold"),
                considered=considered)
        chosen = same[0]

    merged = dict(chosen.params)
    for k, v in params.items():
        if k in merged and k not in ("lhs", "sel", "state_reg", "signal",
                                     "cut_after"):
            merged[k] = v
    chosen = T.TransformSite(**{**chosen.__dict__, "params": merged})
    return Proposal(chosen, reason or chosen.rationale, backend.name, raw=raw,
                    considered=considered)
