"""Standalone RTL optimisation advisor -- suggestions for *any* RTL, no toolchain.

The closed loop in :mod:`genrtl.loop` needs Yosys and OpenSTA: it measures a
real critical path, localises it, proposes one catalogued transform and *proves*
the rewrite before accepting it. That is the right thing to do when you can run
the tools and you want a guarantee.

This module answers a different, lighter question -- "here is a piece of RTL,
what could be optimised?" -- and answers it with nothing but the Python standard
library, so it runs anywhere, on any file, in a fraction of a second. It is the
"GenAI recommends RTL optimisations" surface of the project.

It reports two kinds of finding:

  * ``auto`` findings come from the same verified transform library the closed
    loop uses (:mod:`genrtl.transforms`). Each one is a rewrite the engine can
    apply *and formally prove equivalent*. These are the highest-confidence
    suggestions.

  * ``advisory`` findings come from a broad catalogue of lexical pattern
    detectors below. They cover many more optimisation and correctness
    categories than the eight verified transforms -- strength reduction,
    priority-chain flattening, comparator decoding, resource sharing, common
    sub-expression elimination, FSM/pipelining hints, and a set of RTL
    correctness checks (blocking-in-sequential, latch inference, incomplete
    sensitivity, multi-bit CDC hazards). They are recommendations for a human
    (or an LLM) to act on, not auto-applied.

Every finding carries a category, a severity, the file and line, the offending
snippet, a rationale, a concrete suggested action, and the expected benefit.

An optional LLM pass (:func:`narrate`, reusing the backends in
:mod:`genrtl.proposer`) turns the findings for a module into a short natural
language optimisation review; it is never required and degrades to nothing when
no API key is present.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import rtlparse as rp
from . import transforms as T

# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}

#: category -> (severity, human title) for the verified transforms
_AUTO_META = {
    "pipeline_insert": ("high", "pipelining"),
    "register_retime": ("medium", "pipelining"),
    "balanced_adder_tree": ("medium", "combinational_depth"),
    "logic_restructure": ("medium", "combinational_depth"),
    "balanced_mux_tree": ("medium", "combinational_depth"),
    "boolean_factor": ("medium", "logic_factoring"),
    "fsm_reencode": ("medium", "fsm_encoding"),
    "resource_duplication": ("medium", "fanout"),
}


@dataclass
class Finding:
    category: str
    severity: str           # high | medium | low | info
    module: str
    file: str
    line: int
    title: str
    rationale: str
    suggestion: str
    benefit: str = ""
    snippet: str = ""
    auto: bool = False              # appliable + formally provable by the engine
    transform: str = ""             # verified transform name, when auto
    proof: str = ""                 # equivalence method, when auto
    params: dict = field(default_factory=dict)

    @property
    def rank(self) -> tuple:
        # auto findings and higher severities sort first
        return (-SEVERITY_RANK.get(self.severity, 0), 0 if self.auto else 1,
                self.category, self.line)

    def to_dict(self) -> dict:
        return {
            "category": self.category, "severity": self.severity,
            "module": self.module, "file": self.file, "line": self.line,
            "title": self.title, "rationale": self.rationale,
            "suggestion": self.suggestion, "benefit": self.benefit,
            "snippet": self.snippet, "auto_appliable": self.auto,
            "transform": self.transform, "proof": self.proof,
            "params": self.params,
        }


# ---------------------------------------------------------------------------
# Small lexical helpers (self-contained; complement genrtl.rtlparse)
# ---------------------------------------------------------------------------
_POW2 = lambda v: isinstance(v, int) and v > 1 and (v & (v - 1)) == 0

_NUM_TOKEN = re.compile(r"\d+'[sS]?[bodhBODH][0-9a-fA-F_xzXZ]+|\d+")


def _num(tok: str) -> Optional[int]:
    return rp.eval_literal(tok.strip())


def _snippet(file_lines: List[str], line: int) -> str:
    if 1 <= line <= len(file_lines):
        return file_lines[line - 1].strip()
    return ""


@dataclass
class _Block:
    start: int              # offset in body
    end: int
    kind: str               # "seq" | "comb" | "comb_list" | "latch"
    header: str


def _always_blocks(scan: str) -> List[_Block]:
    """Classified spans of every ``always`` block in a comment-stripped body."""
    out: List[_Block] = []
    for m in re.finditer(r"\balways(?:_ff|_comb|_latch)?\b", scan):
        kw = m.group(0)
        i = m.end()
        header = ""
        # sensitivity list, if written
        rest = scan[i:]
        if rest.lstrip().startswith("@"):
            at = scan.index("@", i)
            j = at + 1
            if scan[j:].lstrip().startswith("("):
                j = scan.index("(", j)
                depth = 0
                while j < len(scan):
                    if scan[j] == "(":
                        depth += 1
                    elif scan[j] == ")":
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    j += 1
                header = scan[at:j]
                i = j
            else:                       # @* form
                header = "@*"
                i = j
        # classify
        if "posedge" in header or "negedge" in header:
            kind = "seq"
        elif kw == "always_comb" or header in ("@*", "") or "*" in header:
            kind = "comb"
        elif kw == "always_latch":
            kind = "latch"
        else:
            kind = "comb_list"          # explicit list, no edges, not '*'
        # body span
        rest = scan[i:]
        lead = len(rest) - len(rest.lstrip())
        if rest.lstrip().startswith("begin"):
            j = i + lead
            depth = 0
            for k in re.finditer(r"\b(begin|end)\b", scan[j:]):
                depth += 1 if k.group(1) == "begin" else -1
                if depth == 0:
                    out.append(_Block(m.start(), j + k.end(), kind, header))
                    break
        else:
            semi = scan.find(";", i)
            if semi > 0:
                out.append(_Block(m.start(), semi + 1, kind, header))
    return out


def _body_start(block: _Block, scan: str) -> int:
    """Offset of the first statement inside the block (past begin / sensitivity)."""
    b = scan.find("begin", block.start, block.end)
    return b + len("begin") if b >= 0 else block.start


# ---------------------------------------------------------------------------
# Advisory detectors
# ---------------------------------------------------------------------------
class _ModuleScan:
    """Runs every detector over one module and collects findings."""

    def __init__(self, module: str, file: Path, file_lines: List[str],
                 ctx: T.TransformContext):
        self.module = module
        self.file = str(file)
        self.lines = file_lines
        self.ctx = ctx
        self.body = ctx.body
        self.scan = rp.strip_comments(ctx.body)
        # a copy with bit-select / range brackets blanked, so a compile-time
        # width expression like [2*W-1:0] is never mistaken for a datapath
        # multiplier by the strength-reduction detector
        self.scan_nobrk = re.sub(
            r"\[[^\]]*\]", lambda m: " " * len(m.group(0)), self.scan)
        self.findings: List[Finding] = []
        self._auto_lines: set = set()
        self._cmp_lines: set = set()        # lines a comparator-chain owns
        self._seen: set = set()

    # -- emit ---------------------------------------------------------------
    def add(self, category: str, severity: str, off: int, title: str,
            rationale: str, suggestion: str, benefit: str = "") -> None:
        line = self.ctx.line_of(off)
        key = (category, line, title)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(Finding(
            category=category, severity=severity, module=self.module,
            file=self.file, line=line, title=title, rationale=rationale,
            suggestion=suggestion, benefit=benefit,
            snippet=_snippet(self.lines, line)))

    # -- verified transforms (auto) ----------------------------------------
    def scan_verified(self) -> None:
        try:
            sites = T.detect_all(self.ctx)
        except Exception:
            sites = []
        for s in sites:
            sev, cat = _AUTO_META.get(s.transform, ("medium", "optimisation"))
            self._auto_lines.add((cat, s.line))
            gain = (f"~{s.est_gain:.1f}x logic-depth reduction on the matched cone"
                    if s.est_gain and s.est_gain > 1.01 else "shorter critical cone")
            self.findings.append(Finding(
                category=cat, severity=sev, module=self.module, file=self.file,
                line=s.line, title=f"{s.transform}: {T.REGISTRY[s.transform].title}",
                rationale=" ".join(s.rationale.split()),
                suggestion=(f"Apply the verified `{s.transform}` transform "
                            f"(params {json.dumps(s.params)}). The closed loop "
                            f"can apply this automatically and prove it "
                            f"equivalent ({s.equivalence})."),
                benefit=gain, snippet=_snippet(self.lines, s.line),
                auto=True, transform=s.transform, proof=s.equivalence,
                params=s.params))

    def _covered(self, category: str, off: int) -> bool:
        return (category, self.ctx.line_of(off)) in self._auto_lines

    # -- strength reduction -------------------------------------------------
    _MULc = re.compile(r"([)\]\w])\s*\*\s*(" + _NUM_TOKEN.pattern + r")|(" +
                       _NUM_TOKEN.pattern + r")\s*\*\s*([\w(])")
    _DIVc = re.compile(r"([)\]\w])\s*([/%])\s*(" + _NUM_TOKEN.pattern + r")")

    def scan_strength_reduction(self) -> None:
        for m in self._MULc.finditer(self.scan_nobrk):
            tok = m.group(2) or m.group(3)
            v = _num(tok or "")
            if not _POW2(v):
                continue
            sh = v.bit_length() - 1
            self.add("strength_reduction", "low", m.start(),
                     f"multiply by {v} is a shift",
                     f"Multiplication by the power of two {v} synthesises to a "
                     f"multiplier unless the tool strength-reduces it; the exact "
                     f"same value is `x << {sh}`.",
                     f"Replace `x * {v}` with `x << {sh}` (or let the tool do it).",
                     "removes a multiplier / hardened DSP block")
        for m in self._DIVc.finditer(self.scan_nobrk):
            op, tok = m.group(2), m.group(3)
            v = _num(tok or "")
            if not _POW2(v):
                continue
            sh = v.bit_length() - 1
            if op == "/":
                self.add("strength_reduction", "low", m.start(),
                         f"unsigned divide by {v} is a shift",
                         f"Division by the power of two {v} is a very expensive "
                         f"cell; for unsigned operands it equals `x >> {sh}`.",
                         f"Replace `x / {v}` with `x >> {sh}` for unsigned x.",
                         "removes a divider (large, slow)")
            else:
                self.add("strength_reduction", "low", m.start(),
                         f"unsigned mod by {v} is a mask",
                         f"`x % {v}` for a power of two {v} equals the low "
                         f"{sh} bits of x.",
                         f"Replace `x % {v}` with `x & {v - 1}` for unsigned x.",
                         "removes a modulo unit")

    # -- comparator OR-chain -> decode/range --------------------------------
    def scan_comparator_chain(self) -> None:
        for a in rp.find_assignments(self.body):
            rhs = rp.strip_comments(a.rhs, keep_len=False)
            terms = rp.split_top_level(rhs, "||")
            if len(terms) < 2:
                terms = rp.split_top_level(rhs, "|")
            if len(terms) < 3:
                continue
            sel, vals, ok = None, [], True
            for t in terms:
                mm = re.match(r"^\(?\s*([A-Za-z_][\w$]*(?:\[[^\]]*\])?)\s*==\s*"
                              r"([^)|]+?)\s*\)?$", t.strip())
                if not mm:
                    ok = False
                    break
                s = mm.group(1)
                sel = sel or s
                if s != sel:
                    ok = False
                    break
                vals.append(mm.group(2).strip())
            if ok and sel and len(vals) >= 3:
                self._cmp_lines.add(self.ctx.line_of(a.start))
                self.add("comparator_chain", "medium", a.start,
                         f"OR of {len(vals)} equality tests on '{sel}'",
                         f"'{a.lhs}' is true when '{sel}' equals any of "
                         f"{', '.join(vals)}. Synthesised literally this is "
                         f"{len(vals)} comparators feeding an OR tree.",
                         f"Decode '{sel}' once (a `case`/one-hot decode) or use a "
                         f"range/`inside` test when the values are contiguous.",
                         f"{len(vals)} comparators -> one decode")

    # -- priority ternary chain (advisory; auto covers the full-cover case) --
    def scan_priority_chain(self) -> None:
        for a in rp.find_assignments(self.body):
            chain = rp.parse_ternary_chain(a.rhs, self.ctx.symbols)
            if not chain.ok or len(chain.cases) < 4:
                continue
            if self._covered("combinational_depth", a.start):
                continue        # balanced_mux_tree already reported it as auto
            self.add("priority_chain", "medium", a.start,
                     f"{len(chain.cases)}-deep priority selection on "
                     f"'{chain.sel}'",
                     f"'{a.lhs}' is a chain of {len(chain.cases)} "
                     f"'({chain.sel} == k) ? .. :' tests. Each step adds a mux "
                     f"level, so the last case is {len(chain.cases)} muxes deep.",
                     f"If the cases are mutually exclusive, a `case ({chain.sel})` "
                     f"statement (or a balanced select tree) collapses the "
                     f"priority chain to roughly log2 depth.",
                     f"depth {len(chain.cases)} -> "
                     f"~{max(1, (len(chain.cases)).bit_length() - 1)}")

    # -- long associative reduction chains not caught by the verified layer --
    def scan_reduction_chain(self) -> None:
        names = {"+": "addition", "^": "XOR", "&": "AND", "|": "OR"}
        for a in rp.find_assignments(self.body):
            rhs = rp.strip_comments(a.rhs, keep_len=False)
            if self.ctx.line_of(a.start) in self._cmp_lines:
                continue        # a comparator-decode finding already covers it
            for op in ("+", "^", "&", "|"):
                terms = rp.split_top_level(rhs, op)
                if len(terms) < 4:
                    continue
                if self._covered("combinational_depth", a.start):
                    break
                depth = len(terms) - 1
                bal = max(1, (len(terms) - 1).bit_length())
                if bal >= depth:
                    break
                self.add("combinational_depth", "medium", a.start,
                         f"{len(terms)}-term {names[op]} chain (depth {depth})",
                         f"'{a.lhs}' is a left-associative chain of {len(terms)} "
                         f"'{op}' terms, so its logic depth is {depth}. '{op}' is "
                         f"associative; a balanced tree is depth {bal} with the "
                         f"same function.",
                         f"Rebalance the '{op}' chain into a binary tree. (The "
                         f"verified engine does this automatically when the terms "
                         f"are simple operands.)",
                         f"depth {depth} -> {bal}")
                break

    # -- resource sharing of an expensive operator across branches ----------
    def scan_resource_sharing(self) -> None:
        for a in rp.find_assignments(self.body):
            rhs = rp.strip_comments(a.rhs, keep_len=False)
            if "?" not in rhs:
                continue
            for op, unit in (("*", "multiplier"), ("/", "divider")):
                # count op occurrences that look like real operators
                cnt = len(re.findall(rf"[)\]\w]\s*\{op}\s*[\w(]", rhs))
                if op == "*":
                    cnt -= len(re.findall(r"\*\s*\*", rhs))
                if cnt >= 2:
                    self.add("resource_sharing", "low", a.start,
                             f"{cnt} {unit}s in mutually exclusive branches of "
                             f"'{a.lhs}'",
                             f"'{a.lhs}' instantiates {cnt} '{op}' operators inside "
                             f"a ternary/case, but only one result is selected "
                             f"each cycle, so the {unit}s are never used at once.",
                             f"Mux the operands first and instantiate a single "
                             f"{unit} (`op = sel ? x : y; r = op * k;`).",
                             f"{cnt} {unit}s -> 1")
                    break

    # -- common sub-expression elimination ----------------------------------
    def scan_cse(self) -> None:
        counts: Dict[str, List[int]] = {}
        for m in re.finditer(r"\([^()]{8,}\)", self.scan):
            inner = m.group(0)[1:-1].strip()
            if inner.startswith("*") or inner.endswith("*"):
                continue                # (* attribute *)
            if ";" in inner or "=" in inner:
                continue                # for-loop header / assignment, not an expr
            # require a real binary operation: operand OP operand (operands may
            # be names, slices, concatenations {…} or parenthesised terms)
            if not re.search(r"[\w$\]\}\)]\s*[+\-*/&|^]\s*[\w$(~{]", inner):
                continue
            idents = {i for i in re.findall(r"(?<![\w$.'])[A-Za-z_]\w*", inner)}
            if len(idents) < 2:
                continue                # e.g. a single operand under a reduction
            expr = re.sub(r"\s+", " ", m.group(0))
            counts.setdefault(expr, []).append(m.start())
        for expr, offs in counts.items():
            if len(offs) >= 2:
                self.add("common_subexpression", "low", offs[0],
                         f"expression {expr} computed {len(offs)} times",
                         f"The sub-expression {expr} appears {len(offs)} times in "
                         f"'{self.module}'. Each occurrence is duplicated logic.",
                         "Compute it once into a named wire and reuse it.",
                         f"{len(offs)} copies -> 1")

    # -- pipelining hint for an un-pipelined multiplier ---------------------
    def scan_pipeline_hint(self) -> None:
        if self._auto_has("pipeline_insert"):
            return                      # verified layer already offers it
        mo = re.search(r"[)\]\w]\s*\*\s*[\w(]", self.scan_nobrk)
        has_seq = "posedge" in self.scan
        if not (mo and has_seq):
            return
        flex = rp.annotation(self.ctx.file_text, "latency-flex")
        off = mo.start()
        if flex:
            self.add("pipelining", "medium", max(off, 0),
                     "un-pipelined multiplier with a latency budget",
                     "A multiplier sits in a single-cycle combinational cloud "
                     "and the module declares a latency tolerance "
                     f"(genrtl-latency-flex: {flex}).",
                     "Register the product to split multiply from the downstream "
                     "add/saturate; the verified pipeline_insert transform can do "
                     "this and prove it with a latency-offset miter.",
                     "roughly halves the multiply path")
        else:
            self.add("pipelining", "low", max(off, 0),
                     "un-pipelined multiplier",
                     "A multiplier sits in a single-cycle combinational cloud. "
                     "Pipelining shortens the path but adds a cycle of latency.",
                     "If the consumer tolerates +1 cycle, register the product "
                     "and declare the contract with `// genrtl-latency-flex: 1` "
                     "so the change can be verified.",
                     "roughly halves the multiply path")

    def _auto_has(self, transform: str) -> bool:
        return any(f.transform == transform for f in self.findings if f.auto)

    # -- correctness: blocking '=' in a sequential block --------------------
    def scan_blocking_in_seq(self) -> None:
        for blk in _always_blocks(self.scan):
            if blk.kind != "seq":
                continue
            s = _body_start(blk, self.scan)
            depth = 0
            i = s
            seg = self.scan
            while i < blk.end:
                c = seg[i]
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                elif depth == 0 and c == "=" and seg[i - 1] not in "<>=!" \
                        and seg[i + 1:i + 2] != "=":
                    self.add("blocking_in_sequential", "high", i,
                             "blocking '=' inside a clocked block",
                             "A blocking assignment in an `always @(posedge ..)` "
                             "block creates order-dependent, simulation-only "
                             "behaviour and can mismatch synthesis.",
                             "Use the non-blocking operator `<=` for every "
                             "register assignment in a sequential block.",
                             "prevents sim/synth mismatch")
                    break
                i += 1

    # -- correctness: non-blocking '<=' in a combinational block ------------
    def scan_nonblocking_in_comb(self) -> None:
        for blk in _always_blocks(self.scan):
            if blk.kind not in ("comb", "comb_list"):
                continue
            seg = self.scan[_body_start(blk, self.scan):blk.end]
            m = re.search(r"[A-Za-z_]\w*(?:\s*\[[^\]]*\])?\s*<=", seg)
            if m:
                self.add("nonblocking_in_combinational", "high",
                         _body_start(blk, self.scan) + m.start(),
                         "non-blocking '<=' inside a combinational block",
                         "A non-blocking assignment in a combinational "
                         "`always @(*)` block schedules the update for the end of "
                         "the time step, which is not what combinational logic "
                         "does and can cause sim/synth mismatch.",
                         "Use blocking '=' for combinational assignments.",
                         "prevents sim/synth mismatch")

    # -- correctness: latch inference in combinational logic ----------------
    def scan_latch_risk(self) -> None:
        for blk in _always_blocks(self.scan):
            if blk.kind not in ("comb", "comb_list"):
                continue
            seg = self.scan[blk.start:blk.end]
            for cm in re.finditer(r"\bcase[zx]?\s*\(", seg):
                end = seg.find("endcase", cm.end())
                if end < 0:
                    continue
                if not re.search(r"\bdefault\s*:", seg[cm.end():end]):
                    self.add("latch_inference", "medium", blk.start + cm.start(),
                             "combinational `case` without `default`",
                             "A `case` in a combinational block that does not "
                             "assign every target in every branch (no `default`) "
                             "infers a latch on the un-assigned outputs.",
                             "Add a `default:` arm, or give every output an "
                             "unconditional default assignment at the top of the "
                             "block.",
                             "removes an unintended latch")

    # -- correctness: incomplete sensitivity list ---------------------------
    def scan_sensitivity(self) -> None:
        for blk in _always_blocks(self.scan):
            if blk.kind != "comb_list":
                continue
            self.add("incomplete_sensitivity", "low", blk.start,
                     "explicit sensitivity list on combinational logic",
                     f"`always {blk.header}` lists signals explicitly; a missing "
                     "signal makes simulation disagree with the synthesised "
                     "hardware.",
                     "Use `always @(*)` (or `always_comb`) so the tool builds the "
                     "sensitivity list.",
                     "prevents sim/synth mismatch")

    # -- correctness: multi-bit CDC without a synchroniser ------------------
    _SYNC_HINT = re.compile(r"sync|_ff\b|meta|gray|handshak|fifo", re.IGNORECASE)

    def scan_multibit_cdc(self) -> None:
        blocks = _always_blocks(self.scan)
        seq = [b for b in blocks if b.kind == "seq"]
        # clock of each sequential block
        def clkof(b: _Block) -> Optional[str]:
            m = re.search(r"posedge\s+([A-Za-z_][\w$]*)", b.header)
            return m.group(1) if m else None
        clocks = {clkof(b) for b in seq} - {None}
        if len(clocks) < 2:
            return
        if self._SYNC_HINT.search(self.module):
            return                      # this module *is* a synchroniser
        # multi-bit regs and the clock they are written under
        widths = {m.group(2): m.group(1)
                  for m in re.finditer(r"\breg\s*(\[[^\]]*\])\s*"
                                       r"([A-Za-z_][\w$]*)", self.scan)}
        written: Dict[str, Optional[str]] = {}
        for b in seq:
            seg = self.scan[b.start:b.end]
            for wm in re.finditer(r"([A-Za-z_][\w$]*)\s*(?:\[[^\]]*\])?\s*<=", seg):
                written.setdefault(wm.group(1), clkof(b))
        for b in seq:
            ck = clkof(b)
            seg = self.scan[_body_start(b, self.scan):b.end]
            reads = set(re.findall(r"(?<![\w$.])([A-Za-z_][\w$]*)", seg))
            for sig in reads:
                if sig not in widths:
                    continue
                src = written.get(sig)
                if src and ck and src != ck and not self._SYNC_HINT.search(sig):
                    self.add("cdc_multibit", "medium", b.start,
                             f"multi-bit '{sig}' read across clock domains",
                             f"'{sig}' {widths[sig]} is written under '{src}' and "
                             f"read under '{ck}'. Sampling a multi-bit bus with an "
                             f"unrelated clock lets its bits resolve on different "
                             f"edges (data incoherence), and a bare flop offers no "
                             f"metastability margin.",
                             "Cross it with a real CDC structure: a gray-coded "
                             "async FIFO for a bus, or a handshake/2-flop "
                             "synchroniser for a qualified word. GenRTL treats "
                             "such structures as protected do-not-touch logic.",
                             "closes a metastability / data-coherence hole")
                    return          # one report per module is enough

    # -- run everything -----------------------------------------------------
    def run(self) -> List[Finding]:
        self.scan_verified()
        for fn in (self.scan_strength_reduction, self.scan_comparator_chain,
                   self.scan_priority_chain, self.scan_reduction_chain,
                   self.scan_resource_sharing, self.scan_cse,
                   self.scan_pipeline_hint, self.scan_blocking_in_seq,
                   self.scan_nonblocking_in_comb, self.scan_latch_risk,
                   self.scan_sensitivity, self.scan_multibit_cdc):
            try:
                fn()
            except Exception:
                continue
        self.findings.sort(key=lambda f: f.rank)
        return self.findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][\w$]*)")


def analyze_text(text: str, filename: str = "<text>",
                 params: Optional[Dict[str, Dict[str, int]]] = None
                 ) -> List[Finding]:
    """Analyse RTL given as a string. Returns findings across every module."""
    params = params or {}
    lines = text.splitlines()
    out: List[Finding] = []
    tmp = Path(filename)
    for m in _MODULE_RE.finditer(rp.strip_comments(text)):
        name = m.group(1)
        ctx = _ctx_from_text(text, tmp, name, params.get(name))
        if ctx is None:
            continue
        out += _ModuleScan(name, tmp, lines, ctx).run()
    out.sort(key=lambda f: (f.file, f.rank))
    return out


def analyze_file(path: Path,
                 params: Optional[Dict[str, Dict[str, int]]] = None
                 ) -> List[Finding]:
    p = Path(path)
    return analyze_text(p.read_text(errors="replace"), str(p), params)


def analyze(paths: List[Path],
            params: Optional[Dict[str, Dict[str, int]]] = None) -> List[Finding]:
    """Analyse a list of files and/or directories of RTL."""
    files: List[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(p.rglob("*.sv")) + sorted(p.rglob("*.v"))
        elif p.is_file():
            files.append(p)
    out: List[Finding] = []
    for f in files:
        out += analyze_file(f, params)
    return out


def _ctx_from_text(text: str, path: Path, module: str,
                   params: Optional[Dict[str, int]]) -> Optional[T.TransformContext]:
    span = rp.module_body(text, module)
    if span is None:
        return None
    ctx = T.TransformContext(path, text, module, span[0], span[1])
    ctx.symbols = rp.localparams(ctx.body)
    ctx.params = dict(params or {})
    return ctx


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_SEV_ICON = {"high": "!!", "medium": " *", "low": "  ", "info": "  "}


def summary_counts(findings: List[Finding]) -> Dict[str, int]:
    c: Dict[str, int] = {"auto": 0, "advisory": 0, "high": 0, "medium": 0,
                         "low": 0, "info": 0}
    for f in findings:
        c["auto" if f.auto else "advisory"] += 1
        c[f.severity] = c.get(f.severity, 0) + 1
    return c


def render_text(findings: List[Finding], color: bool = False) -> str:
    if not findings:
        return "No optimisation opportunities found."
    L: List[str] = []
    counts = summary_counts(findings)
    L.append("=" * 78)
    L.append("  GenRTL optimisation advisor")
    L.append("=" * 78)
    L.append(f"  {len(findings)} findings   "
             f"{counts['auto']} auto-appliable (formally verifiable) + "
             f"{counts['advisory']} advisory")
    L.append(f"  severity: {counts['high']} high, {counts['medium']} medium, "
             f"{counts['low']} low")
    L.append("")
    by_file: Dict[str, List[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)
    for fpath, group in by_file.items():
        L.append(f"# {fpath}")
        for f in group:
            tag = "AUTO" if f.auto else "    "
            L.append(f"  {_SEV_ICON.get(f.severity, '  ')} [{tag}] "
                     f"{f.module}:{f.line}  {f.category}/{f.severity}")
            L.append(f"        {f.title}")
            if f.snippet:
                L.append(f"        | {f.snippet}")
            L.append(f"        why: {f.rationale}")
            L.append(f"        fix: {f.suggestion}")
            if f.benefit:
                L.append(f"        gain: {f.benefit}")
            if f.auto:
                L.append(f"        verified transform: {f.transform}  "
                         f"(proof: {f.proof})")
            L.append("")
    return "\n".join(L)


def render_markdown(findings: List[Finding]) -> str:
    counts = summary_counts(findings)
    L = ["# GenRTL optimisation advisor report", ""]
    L.append(f"**{len(findings)} findings** — {counts['auto']} auto-appliable "
             f"(formally verifiable) + {counts['advisory']} advisory  ")
    L.append(f"Severity: {counts['high']} high · {counts['medium']} medium · "
             f"{counts['low']} low")
    L.append("")
    L.append("| sev | kind | module:line | category | finding | suggested action |")
    L.append("|---|---|---|---|---|---|")
    for f in findings:
        kind = "auto" if f.auto else "advisory"
        title = f.title.replace("|", "\\|")
        sug = f.suggestion.replace("|", "\\|")
        L.append(f"| {f.severity} | {kind} | `{f.module}:{f.line}` | "
                 f"{f.category} | {title} | {sug} |")
    L.append("")
    for f in findings:
        L.append(f"### `{f.module}:{f.line}` — {f.title}  \n")
        L.append(f"- **category:** {f.category} ({f.severity})")
        L.append(f"- **kind:** {'auto-appliable, formally verifiable' if f.auto else 'advisory'}"
                 + (f" — transform `{f.transform}`, proof `{f.proof}`" if f.auto else ""))
        if f.snippet:
            L.append(f"- **code:** `{f.snippet}`")
        L.append(f"- **why:** {f.rationale}")
        L.append(f"- **fix:** {f.suggestion}")
        if f.benefit:
            L.append(f"- **expected benefit:** {f.benefit}")
        L.append("")
    return "\n".join(L)


def to_json(findings: List[Finding]) -> str:
    return json.dumps({
        "summary": summary_counts(findings),
        "findings": [f.to_dict() for f in findings],
    }, indent=2)


# ---------------------------------------------------------------------------
# Optional LLM narration
# ---------------------------------------------------------------------------
_NARRATE_SYSTEM = """\
You are a senior RTL designer reviewing a colleague's module for timing and
quality. You are given the module source and a list of mechanically detected
optimisation opportunities. Write a short, concrete review (a few sentences per
important point) that a designer can act on. Prioritise critical-path / timing
wins. Do not invent issues that are not supported by the code. Do not rewrite
the whole module; recommend specific, minimal changes.
"""


def narrate(module: str, source: str, findings: List[Finding],
            backend=None) -> str:
    """Turn findings for one module into a natural-language review via an LLM.

    ``backend`` is a :class:`genrtl.proposer.Backend`; when it is None (no API
    key / no library) this returns a deterministic textual summary instead, so
    the feature never hard-fails.
    """
    fl = "\n".join(f"- [{'auto' if f.auto else 'advisory'}|{f.severity}] "
                   f"{f.category} at line {f.line}: {f.title} — {f.suggestion}"
                   for f in findings)
    if backend is None:
        return (f"Optimisation summary for {module}: "
                + ("; ".join(f.title for f in findings[:6]) or "no findings")
                + ".")
    user = (f"## Module `{module}`\n\n```systemverilog\n{source}\n```\n\n"
            f"## Detected opportunities\n{fl}\n\n"
            f"Write the review now.")
    try:
        return backend.complete(_NARRATE_SYSTEM, user).strip()
    except Exception as exc:                                    # pragma: no cover
        return f"(LLM narration unavailable: {exc})"
