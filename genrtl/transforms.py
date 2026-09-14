"""Curated library of safe RTL transformations.

The optimiser is deliberately *not* allowed to write arbitrary RTL. It selects
one of the transformations below and supplies its parameters; the rewrite
itself is performed by code in this file, which has a checked precondition and
a declared equivalence obligation. That is what keeps hallucinated edits out of
the design, and it is what makes every patch cheap to prove.

Each transform declares:

    kind          latency_preserving | latency_changing
    equivalence   comb  -- complete SAT proof via the Yosys equivalence engine
                  seq   -- sequential proof (induction, BMC fallback)
                  seq_latency -- sequential proof with an explicit cycle offset
                                 and valid qualification
    detect()      structural pattern match producing concrete parameter sets
    apply()       the rewrite, returning new file text

Transform names map one-to-one onto the eight entries of the project's proposed
transformation library.
"""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import rtlparse as rp

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
LATENCY_PRESERVING = "latency_preserving"
LATENCY_CHANGING = "latency_changing"

EQ_COMB = "comb"
EQ_SEQ = "seq"
EQ_SEQ_LATENCY = "seq_latency"


@dataclass
class TransformContext:
    file_path: Path
    file_text: str
    module: str                 # RTL module name
    module_start: int           # char offset of 'module'
    module_end: int             # char offset past 'endmodule'
    symbols: Dict[str, str] = field(default_factory=dict)
    #: parameter values this module is actually instantiated with, when known
    params: Dict[str, int] = field(default_factory=dict)

    @property
    def body(self) -> str:
        return self.file_text[self.module_start:self.module_end]

    def line_of(self, offset_in_body: int) -> int:
        return self.file_text.count(
            "\n", 0, self.module_start + offset_in_body) + 1

    @classmethod
    def build(cls, file_path: Path, module: str,
              params: Optional[Dict[str, int]] = None
              ) -> Optional["TransformContext"]:
        text = Path(file_path).read_text(errors="replace")
        span = rp.module_body(text, module)
        if span is None:
            return None
        ctx = cls(Path(file_path), text, module, span[0], span[1])
        ctx.symbols = rp.localparams(ctx.body)
        ctx.params = dict(params or {})
        return ctx

    def resolve_int(self, expr: str) -> Optional[int]:
        """Evaluate a small integer expression using params and localparams."""
        env = {**{k: str(v) for k, v in self.params.items()}, **self.symbols}
        v = rp.eval_literal(expr, env)
        if v is not None:
            return v
        try:
            subst = expr
            for name, val in sorted(env.items(), key=lambda kv: -len(kv[0])):
                iv = rp.eval_literal(val, env)
                if iv is None:
                    continue
                subst = re.sub(rf"(?<![\w$]){re.escape(name)}(?![\w$])",
                               str(iv), subst)
            if not re.fullmatch(r"[\d\s()+\-*/%]+", subst):
                return None
            return int(eval(subst, {"__builtins__": {}}, {}))
        except Exception:
            return None


@dataclass
class TransformSite:
    """A concrete, parameterised opportunity found in the RTL."""

    transform: str
    module: str
    file: Path
    line: int
    params: Dict
    rationale: str
    kind: str
    equivalence: str
    latency_delta: int = 0
    #: rough logic-depth reduction factor used only to rank candidates
    est_gain: float = 1.0

    def key(self) -> str:
        p = ",".join(f"{k}={v}" for k, v in sorted(self.params.items())
                     if not isinstance(v, (list, dict)))
        return f"{self.transform}|{self.module}|{self.line}|{p}"

    def to_dict(self) -> dict:
        return {
            "transform": self.transform, "module": self.module,
            "file": str(self.file), "line": self.line, "params": self.params,
            "rationale": self.rationale, "kind": self.kind,
            "equivalence": self.equivalence,
            "latency_delta": self.latency_delta, "est_gain": self.est_gain,
        }


class TransformError(Exception):
    pass


class Transform(ABC):
    name: str = ""
    title: str = ""
    kind: str = LATENCY_PRESERVING
    equivalence: str = EQ_COMB
    description: str = ""
    param_schema: Dict = {}

    @abstractmethod
    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        ...

    @abstractmethod
    def apply(self, ctx: TransformContext, params: Dict) -> str:
        """Return the full new text of ``ctx.file_path``."""

    # -- helpers ------------------------------------------------------------
    def _site(self, ctx: TransformContext, line: int, params: Dict,
              rationale: str, est_gain: float = 1.0,
              latency_delta: int = 0) -> TransformSite:
        return TransformSite(
            transform=self.name, module=ctx.module, file=ctx.file_path,
            line=line, params=params, rationale=rationale, kind=self.kind,
            equivalence=self.equivalence, latency_delta=latency_delta,
            est_gain=est_gain)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
_SIMPLE_TERM = re.compile(
    r"^[~!\-]?\s*(?:[A-Za-z_][\w$]*(?:\s*\[[^\[\]]*\])*|\{[^{}]*\}|"
    r"\d+\s*'\s*[bodhBODH][0-9a-fA-FxzXZ_]+|\d+)$")


def _terms_are_simple(terms: List[str]) -> bool:
    return all(_SIMPLE_TERM.match(t.strip()) for t in terms)


def _replace_span(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


def _word_sub(expr: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![\w$]){re.escape(old)}(?![\w$])", new, expr)


# ---------------------------------------------------------------------------
# 1. logic_restructure -- rebalance an associative bitwise operator nest
# 2. balanced_adder_tree -- rebalance an associative '+' chain
# ---------------------------------------------------------------------------
class _ReductionRebalance(Transform):
    ops: Tuple[str, ...] = ()
    min_terms: int = 4

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        sites: List[TransformSite] = []
        for a in rp.find_assignments(ctx.body):
            for op in self.ops:
                terms = rp.split_top_level(a.rhs, op)
                if len(terms) < self.min_terms:
                    continue
                if not _terms_are_simple(terms):
                    continue
                depth_before = len(terms) - 1
                depth_after = max(1, math.ceil(math.log2(len(terms))))
                if depth_after >= depth_before:
                    continue
                sites.append(self._site(
                    ctx,
                    line=ctx.line_of(a.start),
                    params={"lhs": a.lhs, "op": op, "n_terms": len(terms),
                            "stmt_line": ctx.line_of(a.start)},
                    rationale=(
                        f"'{a.lhs}' is a left-associative chain of {len(terms)} "
                        f"'{op}' terms (logic depth {depth_before}). '{op}' is "
                        f"associative, so the chain can be rebalanced into a "
                        f"depth-{depth_after} tree with an identical "
                        f"combinational function."),
                    est_gain=depth_before / depth_after,
                ))
                break
        return sites

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        lhs = params["lhs"]
        op = params["op"]
        for a in rp.find_assignments(ctx.body):
            if a.lhs != lhs:
                continue
            terms = rp.split_top_level(a.rhs, op)
            if len(terms) < self.min_terms or not _terms_are_simple(terms):
                continue
            new_rhs = rp.balanced_tree([t.strip() for t in terms], op)
            body = _replace_span(
                ctx.body,
                ctx.body.index(a.rhs, a.start), ctx.body.index(a.rhs, a.start) + len(a.rhs),
                new_rhs)
            return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)
        raise TransformError(f"no {op}-chain named {lhs!r} found in {ctx.module}")


class BalancedAdderTree(_ReductionRebalance):
    name = "balanced_adder_tree"
    title = "Balance an adder reduction chain"
    ops = ("+",)
    kind = LATENCY_PRESERVING
    equivalence = EQ_COMB
    description = (
        "Rewrite a left-associative chain of additions as a minimum-depth "
        "binary tree. Integer addition is associative modulo 2**W and Verilog "
        "propagates the context width to every operand of an arithmetic "
        "expression, so the rewrite is bit-exact.")
    param_schema = {
        "type": "object",
        "properties": {
            "lhs": {"type": "string", "description": "left-hand side signal of the chain"},
            "op": {"type": "string", "enum": ["+"]},
        },
        "required": ["lhs"],
    }


class LogicRestructure(_ReductionRebalance):
    name = "logic_restructure"
    title = "Rebalance an associative bitwise operator chain"
    ops = ("^", "|", "&")
    kind = LATENCY_PRESERVING
    equivalence = EQ_COMB
    description = (
        "Rewrite a left-associative chain of ^, | or & as a minimum-depth "
        "binary tree. All three are associative bitwise operators, so the "
        "rewrite changes only the shape of the cone, never its function.")
    param_schema = {
        "type": "object",
        "properties": {
            "lhs": {"type": "string"},
            "op": {"type": "string", "enum": ["^", "|", "&"]},
        },
        "required": ["lhs", "op"],
    }


# ---------------------------------------------------------------------------
# 3. balanced_mux_tree
# ---------------------------------------------------------------------------
class BalancedMuxTree(Transform):
    name = "balanced_mux_tree"
    title = "Convert a priority selection chain into a balanced mux tree"
    kind = LATENCY_PRESERVING
    equivalence = EQ_COMB
    description = (
        "A chain of '(sel == K) ? d_K : ...' over a binary select whose values "
        "cover every code has no priority semantics left to preserve: it is a "
        "one-hot decode feeding an N-deep mux chain. Rewriting it as a binary "
        "tree over the bits of sel cuts the depth from N to log2(N).")
    param_schema = {
        "type": "object",
        "properties": {
            "lhs": {"type": "string", "description": "signal assigned by the chain"},
        },
        "required": ["lhs"],
    }

    def _analyse(self, ctx: TransformContext, a: rp.ContAssign):
        chain = rp.parse_ternary_chain(a.rhs, ctx.symbols)
        if not chain.ok or len(chain.cases) < 5:
            return None
        if not re.match(r"^[A-Za-z_][\w$]*$", chain.sel):
            return None          # need a plain vector we can bit-index
        values = [v for v, _ in chain.cases]
        if len(set(values)) != len(values):
            return None
        n_bits = max(1, (max(values) + 1).bit_length() - 1
                     if (max(values) + 1) & max(values) == 0 else
                     (max(values)).bit_length())
        size = 1 << n_bits
        if len(chain.cases) != size - 1:
            return None
        missing = set(range(size)) - set(values)
        if len(missing) != 1:
            return None
        table: Dict[int, str] = {v: e for v, e in chain.cases}
        table[missing.pop()] = chain.default
        return chain, n_bits, table

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        sites: List[TransformSite] = []
        for a in rp.find_assignments(ctx.body):
            got = self._analyse(ctx, a)
            if got is None:
                continue
            chain, n_bits, table = got
            size = 1 << n_bits
            sites.append(self._site(
                ctx, line=ctx.line_of(a.start),
                params={"lhs": a.lhs, "sel": chain.sel, "n_bits": n_bits},
                rationale=(
                    f"'{a.lhs}' selects among {size} values with a {size}-deep "
                    f"priority chain on '{chain.sel}'. Every code of "
                    f"'{chain.sel}' is covered, so the chain is equivalent to a "
                    f"depth-{n_bits} balanced tree over its bits."),
                est_gain=size / n_bits,
            ))
        return sites

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        lhs = params["lhs"]
        for a in rp.find_assignments(ctx.body):
            if a.lhs != lhs:
                continue
            got = self._analyse(ctx, a)
            if got is None:
                continue
            chain, n_bits, table = got

            def build(bit: int, base: int) -> str:
                if bit < 0:
                    return f"({table[base].strip()})"
                hi = build(bit - 1, base + (1 << bit))
                lo = build(bit - 1, base)
                return f"({chain.sel}[{bit}] ? {hi} : {lo})"

            new_rhs = build(n_bits - 1, 0)
            s = ctx.body.index(a.rhs, a.start)
            body = _replace_span(ctx.body, s, s + len(a.rhs), new_rhs)
            return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)
        raise TransformError(f"no selection chain named {lhs!r} in {ctx.module}")


# ---------------------------------------------------------------------------
# 4. boolean_factor  (Boolean simplification)
# ---------------------------------------------------------------------------
class BooleanFactor(Transform):
    name = "boolean_factor"
    title = "Factor a common term out of a sum of products"
    kind = LATENCY_PRESERVING
    equivalence = EQ_COMB
    description = (
        "Rewrite (a & x) | (a & y) | (a & z) as a & (x | y | z). Distribution "
        "over AND/OR is exact; factoring removes N-1 AND gates and shortens the "
        "cone from the shared term to the output.")
    param_schema = {
        "type": "object",
        "properties": {"lhs": {"type": "string"}},
        "required": ["lhs"],
    }

    def _analyse(self, a: rp.ContAssign):
        products = rp.split_top_level(a.rhs, "|")
        if len(products) < 3:
            return None
        factored: List[List[str]] = []
        for p in products:
            fs = [f.strip() for f in rp.split_top_level(p.strip().strip("()"), "&")]
            if len(fs) < 2 or not _terms_are_simple(fs):
                return None
            factored.append(fs)
        common = set(factored[0])
        for fs in factored[1:]:
            common &= set(fs)
        if not common:
            return None
        # prefer the factor that appears first in the first product
        shared = next(f for f in factored[0] if f in common)
        rests: List[str] = []
        for fs in factored:
            rest = [f for f in fs if f != shared]
            if not rest:
                return None
            rests.append(" & ".join(rest) if len(rest) > 1 else rest[0])
        return shared, rests

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        sites: List[TransformSite] = []
        for a in rp.find_assignments(ctx.body):
            got = self._analyse(a)
            if got is None:
                continue
            shared, rests = got
            sites.append(self._site(
                ctx, line=ctx.line_of(a.start),
                params={"lhs": a.lhs, "shared": shared},
                rationale=(
                    f"'{a.lhs}' is a sum of {len(rests)} products that all "
                    f"contain '{shared}'. Factoring it out removes "
                    f"{len(rests) - 1} AND gates and puts '{shared}' one gate "
                    f"from the output instead of {len(rests)}."),
                est_gain=1.4,
            ))
        return sites

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        lhs = params["lhs"]
        for a in rp.find_assignments(ctx.body):
            if a.lhs != lhs:
                continue
            got = self._analyse(a)
            if got is None:
                continue
            shared, rests = got
            new_rhs = f"{shared} & (" + " | ".join(rests) + ")"
            s = ctx.body.index(a.rhs, a.start)
            body = _replace_span(ctx.body, s, s + len(a.rhs), new_rhs)
            return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)
        raise TransformError(f"no factorable SOP named {lhs!r} in {ctx.module}")


# ---------------------------------------------------------------------------
# 5. fsm_onehot_reencode
# ---------------------------------------------------------------------------
_CASE_RE = re.compile(r"\bcase\s*\(\s*([A-Za-z_][\w$]*)\s*\)", re.MULTILINE)
_DECL_RE_TMPL = r"\b(reg|wire|logic)\s*(\[[^\]]*\])?\s*(?<![\w$]){name}(?![\w$])"


class FsmOnehotReencode(Transform):
    name = "fsm_reencode"
    title = "Re-encode a densely encoded FSM as one-hot"
    kind = LATENCY_PRESERVING
    equivalence = EQ_SEQ
    description = (
        "Replace binary state codes with one-hot codes. Every 'state == S_x' "
        "test collapses from an N-bit comparator to a single bit read, which "
        "shortens the next-state and output cones. State encoding is internal, "
        "so the module's outputs are unchanged -- proved by sequential "
        "equivalence rather than assumed.")
    param_schema = {
        "type": "object",
        "properties": {
            "state_reg": {"type": "string", "description": "name of the state register"},
            "states": {"type": "array", "items": {"type": "string"},
                       "description": "localparam names of the states, in order"},
        },
        "required": ["state_reg"],
    }

    def _analyse(self, ctx: TransformContext):
        body = rp.strip_comments(ctx.body)
        syms = ctx.symbols
        for m in _CASE_RE.finditer(body):
            state_reg = m.group(1)
            end = body.find("endcase", m.end())
            if end < 0:
                continue
            items = re.findall(r"^\s*([A-Za-z_][\w$]*)\s*:", body[m.end():end],
                               re.MULTILINE)
            names = [i for i in items if i in syms]
            if len(names) < 5:
                continue
            values = {n: rp.eval_literal(syms[n]) for n in names}
            if any(v is None for v in values.values()):
                continue
            if len(set(values.values())) != len(values):
                continue
            # all localparams that share the state prefix, ordered by value
            prefix = _common_prefix(names)
            family = {n: rp.eval_literal(v, syms) for n, v in syms.items()
                      if prefix and n.startswith(prefix)}
            family = {n: v for n, v in family.items() if v is not None}
            if len(family) < len(names):
                family = values
            ordered = [n for n, _ in sorted(family.items(), key=lambda kv: kv[1])]
            return state_reg, ordered
        return None

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        got = self._analyse(ctx)
        if got is None:
            return []
        state_reg, states = got
        n = len(states)
        old_bits = max(1, (n - 1).bit_length())
        return [self._site(
            ctx, line=ctx.line_of(rp.strip_comments(ctx.body).find(f"case ({state_reg}")),
            params={"state_reg": state_reg, "states": states},
            rationale=(
                f"'{state_reg}' holds {n} states in {old_bits} bits, so every "
                f"branch guard is an {old_bits}-bit comparator. One-hot "
                f"encoding turns each guard into a single bit read at the cost "
                f"of {n - old_bits} extra flops."),
            est_gain=1.6,
        )]

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        got = self._analyse(ctx)
        if got is None:
            raise TransformError(f"no dense FSM found in {ctx.module}")
        state_reg, states = got
        states = params.get("states") or states
        n = len(states)
        body = ctx.body

        # 1. rewrite the localparam values
        for i, name in enumerate(states):
            onehot = f"{n}'b" + "".join("1" if b == i else "0"
                                        for b in range(n - 1, -1, -1))
            pat = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])\s*=\s*[^,;\n]+")
            body, cnt = pat.subn(f"{name} = {onehot}", body, count=1)
            if cnt == 0:
                raise TransformError(f"could not rewrite state constant {name}")

        # 2. widen the localparam declaration and the state variables
        body = re.sub(r"(\blocalparam\s*)\[[^\]]*\]", rf"\g<1>[{n - 1}:0]", body,
                      count=1)
        widened = 0
        for var in _state_vars(body, state_reg):
            pat = re.compile(_DECL_RE_TMPL.format(name=re.escape(var)))
            new_body, cnt = pat.subn(rf"\g<1> [{n - 1}:0] {var}", body, count=1)
            if cnt:
                body = new_body
                widened += 1
        if widened == 0:
            raise TransformError(f"could not widen the declaration of {state_reg}")

        body = body.replace(
            "\nendmodule",
            f"\n  // genrtl: FSM re-encoded to one-hot ({n} states)\nendmodule", 1)
        return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)


def _common_prefix(names: List[str]) -> str:
    if not names:
        return ""
    p = names[0]
    for n in names[1:]:
        while not n.startswith(p) and p:
            p = p[:-1]
    return p


def _state_vars(body: str, state_reg: str) -> List[str]:
    """The state register plus any same-width 'next state' companion."""
    out = [state_reg]
    base = state_reg[:-2] if state_reg.endswith(("_r", "_q")) else state_reg
    for suffix in ("_c", "_n", "_nxt", "_next", "_d"):
        cand = base + suffix
        if re.search(rf"(?<![\w$]){re.escape(cand)}(?![\w$])", body):
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# 6. resource_duplication
# ---------------------------------------------------------------------------
class ResourceDuplication(Transform):
    name = "resource_duplication"
    title = "Duplicate a high-fanout register and split its loads"
    kind = LATENCY_PRESERVING
    equivalence = EQ_SEQ
    description = (
        "A register that drives a very wide load has a slow output transition, "
        "which lands on every path that starts at it. Replicating the flop and "
        "giving each copy a share of the loads divides the capacitance. The "
        "copies have identical next-state functions, so equivalence follows by "
        "induction.")
    param_schema = {
        "type": "object",
        "properties": {
            "signal": {"type": "string", "description": "register to duplicate"},
            "copies": {"type": "integer", "minimum": 2, "maximum": 8},
        },
        "required": ["signal"],
    }

    _TARGET_RE = re.compile(
        r"genrtl-target:\s*high_fanout_reg\s+name=(?P<name>[\w$]+)")

    def _candidates(self, ctx: TransformContext) -> List[Tuple[str, int]]:
        body = ctx.body
        out: List[Tuple[str, int]] = []
        # annotated candidates first
        for m in self._TARGET_RE.finditer(body):
            out.append((m.group("name"), body.count(m.group("name"))))
        # then any register read many times inside the module
        for m in re.finditer(r"\breg\s*(?:\[[^\]]*\])?\s*([A-Za-z_][\w$]*)\s*;", body):
            name = m.group(1)
            uses = len(re.findall(rf"(?<![\w$]){re.escape(name)}(?![\w$])", body))
            if uses >= 6 and name not in [o[0] for o in out]:
                out.append((name, uses))
        return out

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        sites: List[TransformSite] = []
        for name, uses in self._candidates(ctx):
            if not re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])\s*<=", ctx.body):
                continue
            # Precondition: exactly one always block drives the register, and
            # that block drives nothing else. Otherwise duplicating the block
            # would create multiple drivers for the other signals.
            if _sole_driver_block(ctx.body, name) is None:
                continue
            sites.append(self._site(
                ctx,
                line=ctx.line_of(ctx.body.index(name)),
                params={"signal": name, "copies": 2},
                rationale=(
                    f"'{name}' is a register read {uses} times inside "
                    f"{ctx.module}; its output net carries the whole load. "
                    f"Duplicating the flop halves the capacitance each copy has "
                    f"to drive."),
                est_gain=1.3,
            ))
        return sites

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        name = params["signal"]
        copies = int(params.get("copies", 2))
        if copies < 2 or copies > 8:
            raise TransformError("copies must be between 2 and 8")
        body = ctx.body

        scan = rp.strip_comments(body)
        decl = re.search(
            rf"\breg\s*(?P<w>\[[^\]]*\])?\s*(?<![\w$]){re.escape(name)}(?![\w$])\s*;",
            scan)
        if not decl:
            raise TransformError(f"{name!r} is not a simple reg declaration")
        width = decl.group("w") or ""

        span = _sole_driver_block(body, name)
        if span is None:
            raise TransformError(
                f"{name!r} is not driven by exactly one dedicated always block")
        b0, b1 = span

        new_names = [f"{name}__dup{i}" for i in range(1, copies)]
        all_names = [name] + new_names

        # Every edit is computed against the ORIGINAL body and applied back to
        # front, so no offset is ever invalidated by an earlier rewrite.
        edits: List[Tuple[int, int, str]] = []

        extra_decls = "".join(
            f"\n  (* keep *) reg {width} {nn};" for nn in new_names)
        edits.append((decl.start(), decl.end(),
                      body[decl.start():decl.end()]
                      + "  // genrtl: duplicated to split fanout"
                      + extra_decls))

        # Replicate the whole driving block, renaming only the assignment
        # target. Each copy therefore computes the identical next state.
        block = body[b0:b1]
        dup_blocks = ""
        for nn in new_names:
            dup = re.sub(
                rf"(?<![\w$]){re.escape(name)}(?![\w$])(\s*<=)", rf"{nn}\1", block)
            dup_blocks += "\n\n  " + dup.strip()
        edits.append((b0, b1, block + dup_blocks))

        # Split the read sites round-robin. Reads inside the driving block stay
        # on the original signal so the copies never diverge.
        k = 0
        for m in re.finditer(rf"(?<![\w$]){re.escape(name)}(?![\w$])", scan):
            if decl.start() <= m.start() < decl.end():
                continue
            if b0 <= m.start() < b1:
                continue
            k += 1
            tgt = all_names[k % copies]
            if tgt != name:
                edits.append((m.start(), m.end(), tgt))

        for start, end, rep in sorted(edits, key=lambda e: -e[0]):
            body = body[:start] + rep + body[end:]

        return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)


def _always_blocks(body: str) -> List[Tuple[int, int]]:
    """Character spans of every ``always`` block in ``body``."""
    scan = rp.strip_comments(body)
    spans: List[Tuple[int, int]] = []
    for m in re.finditer(r"\balways(?:_ff|_comb|_latch)?\b", scan):
        i = m.end()
        # skip the sensitivity list
        if scan[i:].lstrip().startswith("@"):
            i = scan.index("@", i) + 1
            if scan[i:].lstrip().startswith("("):
                i = scan.index("(", i)
                depth = 0
                while i < len(scan):
                    if scan[i] == "(":
                        depth += 1
                    elif scan[i] == ")":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    i += 1
        rest = scan[i:]
        lead = len(rest) - len(rest.lstrip())
        if rest.lstrip().startswith("begin"):
            j = i + lead
            depth = 0
            for k in re.finditer(r"\b(begin|end)\b", scan[j:]):
                depth += 1 if k.group(1) == "begin" else -1
                if depth == 0:
                    spans.append((m.start(), j + k.end()))
                    break
        else:
            semi = scan.find(";", i)
            if semi > 0:
                spans.append((m.start(), semi + 1))
    return spans


def _sole_driver_block(body: str, name: str) -> Optional[Tuple[int, int]]:
    """Span of the one ``always`` block that drives ``name`` and nothing else."""
    scan = rp.strip_comments(body)
    hits: List[Tuple[int, int]] = []
    for a, b in _always_blocks(body):
        targets = set(re.findall(r"([A-Za-z_][\w$]*)\s*(?:\[[^\]]*\])?\s*<=",
                                 scan[a:b]))
        if name in targets:
            hits.append((a, b))
            if targets != {name}:
                return None
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# 7. pipeline_insert   (latency changing)
# 8. register_retime   (latency preserving, moves an inserted stage)
# ---------------------------------------------------------------------------
_PORT_RE = re.compile(
    r"\b(?P<dir>input|output|inout)\s+(?:wire|reg|logic)?\s*"
    r"(?P<width>\[[^\]]*\])?\s*(?P<name>[A-Za-z_][\w$]*)\s*(?=,|\)|;)")


def _ports(ctx: TransformContext) -> Dict[str, Tuple[str, str]]:
    header_end = ctx.body.find(");")
    header = ctx.body[:header_end if header_end > 0 else len(ctx.body)]
    out: Dict[str, Tuple[str, str]] = {}
    for m in _PORT_RE.finditer(rp.strip_comments(header)):
        out[m.group("name")] = (m.group("dir"), (m.group("width") or "").strip())
    return out


_WIRE_DECL_RE = re.compile(
    r"^[ \t]*wire\s*(?P<width>\[[^\]]*\])?\s*(?P<name>[A-Za-z_][\w$]*)\s*=\s*"
    r"(?P<rhs>[^;]+);", re.MULTILINE)

PIPE_MARK = "genrtl-pipeline-stage"


@dataclass
class _ChainItem:
    name: str
    width: str
    rhs: str
    start: int
    end: int


def _generate_spans(scan: str) -> List[Tuple[int, int]]:
    """Character spans of generate/genvar-loop regions.

    Wires declared inside a generate block are replicated per iteration, so
    they are not a linear combinational chain and must never be offered to
    pipeline_insert.
    """
    spans: List[Tuple[int, int]] = []
    for m in re.finditer(r"\bgenerate\b", scan):
        e = scan.find("endgenerate", m.end())
        spans.append((m.start(), (e + len("endgenerate")) if e > 0 else len(scan)))
    return spans


def _in_spans(pos: int, spans: List[Tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _wire_chain(ctx: TransformContext) -> List[_ChainItem]:
    scan = rp.strip_comments(ctx.body)
    spans = _generate_spans(scan)
    out: List[_ChainItem] = []
    for m in _WIRE_DECL_RE.finditer(scan):
        if _in_spans(m.start(), spans):
            continue
        out.append(_ChainItem(
            name=m.group("name"), width=(m.group("width") or "").strip(),
            rhs=ctx.body[m.start("rhs"):m.end("rhs")].strip(),
            start=m.start(), end=m.end()))
    return out


def _idents(expr: str) -> List[str]:
    return re.findall(r"(?<![\w$.'])([A-Za-z_][\w$]*)", expr)


@dataclass
class _ChainArray:
    """An unrolled ``wire [W] name [0:N]`` pipeline written with a generate."""
    array: str
    upper: str            # the '0:UPPER' bound, e.g. "ROUNDS"
    width: str
    genvar: str
    final_reg: str
    decl_end: int         # offset just past the array declaration


_ARRAY_DECL_RE = re.compile(
    r"^[ \t]*wire\s*(?P<width>\[[^\]]*\])\s*(?P<name>[A-Za-z_][\w$]*)\s*"
    r"\[\s*0\s*:\s*(?P<upper>[^\]]+?)\s*\]\s*;", re.MULTILINE)


def _chain_array(ctx: TransformContext) -> Optional[_ChainArray]:
    scan = rp.strip_comments(ctx.body)
    for m in _ARRAY_DECL_RE.finditer(scan):
        name, upper = m.group("name"), m.group("upper").strip()
        if not re.search(rf"assign\s+{re.escape(name)}\s*\[\s*0\s*\]\s*=", scan):
            continue
        step = re.search(
            rf"assign\s+{re.escape(name)}\s*\[\s*([A-Za-z_][\w$]*)\s*\+\s*1\s*\]\s*=",
            scan)
        if not step:
            continue
        final = re.search(
            rf"([A-Za-z_][\w$]*)\s*<=\s*{re.escape(name)}\s*\[\s*"
            rf"{re.escape(upper)}\s*\]\s*;", scan)
        if not final:
            continue
        return _ChainArray(array=name, upper=upper, width=m.group("width"),
                           genvar=step.group(1), final_reg=final.group(1),
                           decl_end=m.end())
    return None


class PipelineInsert(Transform):
    name = "pipeline_insert"
    title = "Insert a pipeline stage into a combinational chain"
    kind = LATENCY_CHANGING
    equivalence = EQ_SEQ_LATENCY
    description = (
        "Cut a combinational chain and register the signals that cross the "
        "cut, delaying the valid qualifier by the same amount. Two chain "
        "shapes are recognised: a linear sequence of wire assignments "
        "(params: cut_after), and an unrolled generate pipeline over a wire "
        "array (params: cut_stage). Only legal in modules that publish a "
        "latency contract (genrtl-latency-flex) and expose a valid output. "
        "Verified with a latency-offset sequential miter, not assumed.")
    param_schema = {
        "type": "object",
        "properties": {
            "cut_after": {"type": "string",
                          "description": "name of the last wire that stays in stage 1"},
            "cut_stage": {"type": "string",
                          "description": "constant expression for the generate "
                                         "stage to register after, e.g. \"ROUNDS/2\""},
        },
    }

    valid_in = "in_valid"
    valid_out = "out_valid"

    def _flex(self, ctx: TransformContext) -> int:
        v = rp.annotation(ctx.file_text, "latency-flex")
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return 0

    def _current_stages(self, ctx: TransformContext) -> int:
        return ctx.body.count(PIPE_MARK)

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        flex = self._flex(ctx)
        if flex <= 0:
            return []
        if self._current_stages(ctx) >= flex:
            return []
        ports = _ports(ctx)
        if self.valid_in not in ports or self.valid_out not in ports:
            return []
        sites: List[TransformSite] = []

        arr = _chain_array(ctx)
        if arr is not None and (ctx.resolve_int(arr.upper) or 2) >= 2:
            sites.append(self._site(
                ctx, line=ctx.line_of(arr.decl_end),
                params={"cut_stage": f"({arr.upper})/2"},
                rationale=(
                    f"'{arr.array}' is an unrolled generate pipeline of "
                    f"{arr.upper} stages evaluated in a single cycle before "
                    f"'{arr.final_reg}'. Registering the midpoint halves the "
                    f"combinational depth; the module declares a latency "
                    f"tolerance of {flex} cycle(s)."),
                est_gain=2.0, latency_delta=1))

        chain = _wire_chain(ctx)
        if len(chain) < 2:
            return sites
        for i in range(len(chain) - 1):
            sites.append(self._site(
                ctx, line=ctx.line_of(chain[i].start),
                params={"cut_after": chain[i].name},
                rationale=(
                    f"The combinational chain "
                    f"{' -> '.join(c.name for c in chain)} runs between the "
                    f"input registers and the output register. Cutting after "
                    f"'{chain[i].name}' splits it into "
                    f"{i + 1} and {len(chain) - i - 1} levels; the module "
                    f"declares a latency tolerance of {flex} cycle(s)."),
                est_gain=len(chain) / max(1, max(i + 1, len(chain) - i - 1)),
                latency_delta=1,
            ))
        return sites

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        flex = self._flex(ctx)
        if flex <= 0:
            raise TransformError(f"{ctx.module} declares no latency tolerance")
        if self._current_stages(ctx) >= flex:
            raise TransformError(
                f"{ctx.module} already uses its declared latency budget")

        if params.get("cut_stage") and not params.get("cut_after"):
            return self._apply_chain_array(ctx, str(params["cut_stage"]))

        cut_after = params["cut_after"]
        chain = _wire_chain(ctx)
        names = [c.name for c in chain]
        if cut_after not in names:
            raise TransformError(f"{cut_after!r} is not a wire in the chain")
        cut = names.index(cut_after)
        if cut >= len(chain) - 1:
            raise TransformError("cut must leave at least one downstream stage")

        ports = _ports(ctx)
        stage = self._current_stages(ctx) + 1
        sfx = f"_p{stage}"

        upstream = {c.name: c for c in chain[:cut + 1]}
        downstream = chain[cut + 1:]

        # every non-blocking RHS in the module also counts as downstream logic
        tail_start = downstream[0].start
        tail_text = ctx.body[tail_start:]

        used: List[str] = []
        for c in downstream:
            used += _idents(c.rhs)
        for m in re.finditer(r"<=\s*([^;]+);", rp.strip_comments(tail_text)):
            used += _idents(m.group(1))

        crossing: List[Tuple[str, str]] = []       # (name, width)
        for nm in used:
            if nm in [c[0] for c in crossing]:
                continue
            if nm in upstream:
                crossing.append((nm, upstream[nm].width))
            elif nm in ports and ports[nm][0] == "input" and nm != self.valid_in:
                crossing.append((nm, ports[nm][1]))
        if not crossing:
            raise TransformError("no signals cross the proposed cut")

        vreg = f"genrtl_valid{sfx}"
        rst = _reset_signal(ctx.body)

        decls = "".join(
            f"  reg {w + ' ' if w else ''}{nm}{sfx};\n" for nm, w in crossing)
        rst_body = "".join(f"      {nm}{sfx} <= '0;\n" for nm, _ in crossing)
        set_body = "".join(
            f"        {nm}{sfx} <= {nm};\n" for nm, _ in crossing)

        block = (
            f"\n  // >>> {PIPE_MARK} {stage}: inserted by the GenAI optimiser\n"
            f"{decls}"
            f"  reg {vreg};\n"
            f"  always @(posedge clk or negedge {rst}) begin\n"
            f"    if (!{rst}) begin\n"
            f"{rst_body}"
            f"      {vreg} <= 1'b0;\n"
            f"    end else begin\n"
            f"      {vreg} <= {self.valid_in};\n"
            f"      if ({self.valid_in}) begin\n"
            f"{set_body}"
            f"      end\n"
            f"    end\n"
            f"  end\n"
            f"  // <<< {PIPE_MARK} {stage}\n"
        )

        # rewrite downstream references
        new_tail = tail_text
        for nm, _ in crossing:
            new_tail = _word_sub(new_tail, nm, f"{nm}{sfx}")
        new_tail = _word_sub(new_tail, self.valid_in, vreg)

        body = ctx.body[:tail_start] + block + new_tail
        return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)

    # -- generate-array pipelines -------------------------------------------
    def _apply_chain_array(self, ctx: TransformContext, cut_expr: str) -> str:
        arr = _chain_array(ctx)
        if arr is None:
            raise TransformError(
                f"{ctx.module} has no unrolled generate pipeline to cut")
        stage = self._current_stages(ctx) + 1
        sfx = f"_p{stage}"
        rst = _reset_signal(ctx.body)
        a, w = arr.array, arr.width
        shadow = f"{a}_genrtl_s{stage}"
        pipe = f"genrtl_pipe{sfx}"
        vreg = f"genrtl_valid{sfx}"
        at = f"GENRTL_PIPE_AT{sfx.upper()}"
        gp = f"genrtl_gp{stage}"

        block = (
            f"\n  // >>> {PIPE_MARK} {stage}: inserted by the GenAI optimiser\n"
            f"  localparam integer {at} = {cut_expr};\n"
            f"  reg {w} {pipe};\n"
            f"  reg {vreg};\n"
            f"  always @(posedge clk or negedge {rst}) begin\n"
            f"    if (!{rst}) begin\n"
            f"      {pipe} <= '0;\n"
            f"      {vreg} <= 1'b0;\n"
            f"    end else begin\n"
            f"      {vreg} <= {self.valid_in};\n"
            f"      if ({self.valid_in}) {pipe} <= {a}[{at}];\n"
            f"    end\n"
            f"  end\n"
            f"  wire {w} {shadow} [0:{arr.upper}];\n"
            f"  genvar {gp};\n"
            f"  generate\n"
            f"    for ({gp} = 0; {gp} <= {arr.upper}; {gp} = {gp} + 1)"
            f" begin : g_{shadow}\n"
            f"      if ({gp} == {at}) assign {shadow}[{gp}] = {pipe};\n"
            f"      else              assign {shadow}[{gp}] = {a}[{gp}];\n"
            f"    end\n"
            f"  endgenerate\n"
            f"  // <<< {PIPE_MARK} {stage}\n"
        )

        body = ctx.body[:arr.decl_end] + block + ctx.body[arr.decl_end:]

        # Every *read* of the chain array now goes through the shadow copy.
        # Writes (assign name[...] = ...) keep driving the original array.
        out: List[str] = []
        pos = 0
        scan = rp.strip_comments(body)
        blk_end = arr.decl_end + len(block)
        for m in re.finditer(rf"(?<![\w$]){re.escape(a)}(?![\w$])\s*\[", scan):
            if arr.decl_end <= m.start() < blk_end:
                continue                      # inside the block we just added
            line_start = body.rfind("\n", 0, m.start()) + 1
            prefix = scan[line_start:m.start()]
            if re.search(r"\bassign\s*$", prefix) or re.search(r"\bwire\b", prefix):
                continue                      # this is a write, not a read
            out.append(body[pos:m.start()])
            out.append(shadow)
            pos = m.start() + len(a)
        out.append(body[pos:])
        body = "".join(out)

        # Delay the valid qualifier by the same amount.
        fin = re.search(
            rf"if\s*\(\s*{re.escape(self.valid_in)}\s*\)\s*"
            rf"{re.escape(arr.final_reg)}\s*<=", body)
        if fin:
            body = body[:fin.start()] + f"if ({vreg}) {arr.final_reg} <=" \
                   + body[fin.end():]
        body = re.sub(
            rf"(?<![\w$]){re.escape(self.valid_out)}\s*<=\s*"
            rf"{re.escape(self.valid_in)}\s*;",
            f"{self.valid_out} <= {vreg};", body)
        return _replace_span(ctx.file_text, ctx.module_start, ctx.module_end, body)


def _reset_signal(body: str) -> str:
    m = re.search(r"negedge\s+([A-Za-z_][\w$]*)", rp.strip_comments(body))
    return m.group(1) if m else "rst_n"


class RegisterRetime(Transform):
    name = "register_retime"
    title = "Retime an existing pipeline boundary"
    kind = LATENCY_PRESERVING
    equivalence = EQ_SEQ
    description = (
        "Move an already-inserted pipeline register one level later in the "
        "combinational chain. Latency is unchanged, so this rebalances two "
        "adjacent stages instead of adding one. Requires a stage previously "
        "created by pipeline_insert.")
    param_schema = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["forward", "backward"]},
        },
        "required": ["direction"],
    }

    def _stage_span(self, ctx: TransformContext) -> Optional[Tuple[int, int, int]]:
        body = ctx.body
        i = body.find(f"// >>> {PIPE_MARK}")
        if i < 0:
            return None
        j = body.find(f"// <<< {PIPE_MARK}", i)
        if j < 0:
            return None
        j = body.find("\n", j) + 1
        stage = int(re.search(r"stage (\d+)", body[i:i + 120]).group(1)) \
            if re.search(r"stage (\d+)", body[i:i + 120]) else 1
        return i, j, stage

    def detect(self, ctx: TransformContext) -> List[TransformSite]:
        span = self._stage_span(ctx)
        if span is None:
            return []
        i, j, _ = span
        after = _wire_chain_after(ctx, j)
        before = _wire_chain_before(ctx, i)
        sites: List[TransformSite] = []
        if len(after) >= 2:
            sites.append(self._site(
                ctx, line=ctx.line_of(i),
                params={"direction": "forward"},
                rationale=(
                    f"The inserted stage sits after {len(before)} of "
                    f"{len(before) + len(after)} chain levels. Moving it one "
                    f"level later rebalances the two stages."),
                est_gain=1.15))
        if len(before) >= 2:
            sites.append(self._site(
                ctx, line=ctx.line_of(i),
                params={"direction": "backward"},
                rationale=(
                    f"The inserted stage sits after {len(before)} of "
                    f"{len(before) + len(after)} chain levels. Moving it one "
                    f"level earlier rebalances the two stages."),
                est_gain=1.15))
        return sites

    def apply(self, ctx: TransformContext, params: Dict) -> str:
        """Delete the existing stage, then re-insert it one level over."""
        direction = params.get("direction", "forward")
        span = self._stage_span(ctx)
        if span is None:
            raise TransformError(f"{ctx.module} has no inserted pipeline stage")
        i, j, stage = span
        sfx = f"_p{stage}"

        # 1. remove the stage block and undo its downstream renaming
        body = ctx.body[:i] + ctx.body[j:]
        tail = body[i:]
        vreg = f"genrtl_valid{sfx}"
        tail = _word_sub(tail, vreg, PipelineInsert.valid_in)
        for nm in sorted(set(re.findall(rf"([A-Za-z_][\w$]*){re.escape(sfx)}\b",
                                        tail)), key=len, reverse=True):
            tail = _word_sub(tail, f"{nm}{sfx}", nm)
        body = body[:i] + tail
        stripped = _replace_span(ctx.file_text, ctx.module_start,
                                 ctx.module_end, body)

        # 2. re-insert at the neighbouring cut
        tmp = TransformContext(ctx.file_path, stripped, ctx.module,
                               ctx.module_start,
                               ctx.module_start + len(body))
        tmp.symbols = ctx.symbols
        chain = [c.name for c in _wire_chain(tmp)]
        if len(chain) < 2:
            raise TransformError("no combinational chain left to cut")
        # where the removed stage used to sit
        old_cut = max(0, len(_wire_chain_before(ctx, i)) - 1)
        new_cut = old_cut + (1 if direction == "forward" else -1)
        new_cut = max(0, min(new_cut, len(chain) - 2))
        if new_cut == old_cut:
            raise TransformError(
                f"cannot move the stage {direction} any further")
        return PipelineInsert().apply(tmp, {"cut_after": chain[new_cut]})


def _wire_chain_after(ctx: TransformContext, offset: int) -> List[str]:
    scan = rp.strip_comments(ctx.body)
    return [m.group("name") for m in _WIRE_DECL_RE.finditer(scan)
            if m.start() >= offset]


def _wire_chain_before(ctx: TransformContext, offset: int) -> List[str]:
    scan = rp.strip_comments(ctx.body)
    return [m.group("name") for m in _WIRE_DECL_RE.finditer(scan)
            if m.start() < offset]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
REGISTRY: Dict[str, Transform] = {}
for _t in (LogicRestructure(), BalancedAdderTree(), BalancedMuxTree(),
           RegisterRetime(), PipelineInsert(), FsmOnehotReencode(),
           ResourceDuplication(), BooleanFactor()):
    REGISTRY[_t.name] = _t

ORDER = ["logic_restructure", "balanced_adder_tree", "balanced_mux_tree",
         "register_retime", "pipeline_insert", "fsm_reencode",
         "resource_duplication", "boolean_factor"]


def catalogue() -> List[dict]:
    """Machine-readable description of the library, for the LLM prompt."""
    out = []
    for n in ORDER:
        t = REGISTRY[n]
        out.append({
            "name": t.name, "title": t.title, "kind": t.kind,
            "equivalence": t.equivalence, "description": t.description,
            "params": t.param_schema,
        })
    return out


def detect_all(ctx: TransformContext,
               only: Optional[List[str]] = None) -> List[TransformSite]:
    sites: List[TransformSite] = []
    for name in (only or ORDER):
        t = REGISTRY.get(name)
        if t is None:
            continue
        try:
            sites += t.detect(ctx)
        except Exception:
            continue
    sites.sort(key=lambda s: -s.est_gain)
    return sites


def apply_site(site: TransformSite,
               params: Optional[Dict[str, int]] = None) -> str:
    ctx = TransformContext.build(site.file, site.module, params)
    if ctx is None:
        raise TransformError(f"module {site.module} not found in {site.file}")
    return REGISTRY[site.transform].apply(ctx, site.params)
