"""Small, dependency-free SystemVerilog helpers used by the transform library.

This is not a full parser -- it is a set of robust lexical utilities that are
sufficient for the constrained rewrites the optimiser is allowed to make:
comment/string-aware scanning, top-level operator splitting, localparam symbol
tables and continuous-assignment extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Comment / string aware scanning
# ---------------------------------------------------------------------------
def strip_comments(text: str, keep_len: bool = True) -> str:
    """Blank out comments so regexes never match inside them.

    With ``keep_len`` the result has exactly the same length as the input, so
    offsets computed on the stripped text are valid in the original.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append((" " * (j - i)) if keep_len else "")
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank = "".join(ch if ch == "\n" else " " for ch in text[i:j])
            out.append(blank if keep_len else "")
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(text[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_top_level(expr: str, op: str) -> List[str]:
    """Split ``expr`` on ``op`` occurrences that are at bracket depth zero.

    Returns ``[expr]`` when the operator does not appear at the top level.
    Handles (), [], {} and skips the ``?:`` conditional operator's colon.
    """
    parts: List[str] = []
    depth = 0
    i, n = 0, len(expr)
    last = 0
    L = len(op)
    while i < n:
        c = expr[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith(op, i):
            # do not split "&&" when asked for "&", "||" for "|", "^~" for "^"
            nxt = expr[i + L: i + L + 1]
            prv = expr[i - 1: i] if i else ""
            if op in "&|^" and (nxt == op or prv == op):
                i += 1
                continue
            if op == "+" and nxt in "+=":
                i += 1
                continue
            if op == "^" and nxt == "~":
                i += 1
                continue
            parts.append(expr[last:i])
            i += L
            last = i
            continue
        i += 1
    parts.append(expr[last:])
    return [p.strip() for p in parts]


def balanced_tree(terms: List[str], op: str) -> str:
    """Combine ``terms`` into a minimum-depth balanced expression tree."""
    level = [f"({t})" if not _atomic(t) else t for t in terms]
    while len(level) > 1:
        nxt: List[str] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(f"({level[i]} {op} {level[i + 1]})")
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


_ATOMIC_RE = re.compile(r"^[A-Za-z_$][\w$]*(\s*\[[^\[\]]*\])*$|^\d+'[bodhBODH][0-9a-fA-F_xzXZ]+$|^\d+$")


def _atomic(term: str) -> bool:
    t = term.strip()
    if t.startswith("{") and t.endswith("}"):
        return True
    return bool(_ATOMIC_RE.match(t))


# ---------------------------------------------------------------------------
# Module / declaration extraction
# ---------------------------------------------------------------------------
@dataclass
class ContAssign:
    """A continuous assignment or an initialised wire declaration."""
    lhs: str
    rhs: str
    start: int          # character offset of the statement in the file
    end: int            # offset just past the terminating ';'
    line: int           # 1-based line of ``start``
    decl_prefix: str = ""   # e.g. "wire [31:0] " for initialised declarations


_ASSIGN_RE = re.compile(
    r"(?P<prefix>\bassign\s+|\b(?:wire|logic|reg)\b[^;=\n]*?\s)"
    r"(?P<lhs>[A-Za-z_][\w$]*(?:\s*\[[^;]*?\])?)\s*=\s*(?P<rhs>[^;]+?);",
    re.DOTALL,
)


def find_assignments(text: str) -> List[ContAssign]:
    """Find ``assign x = expr;`` and ``wire [..] x = expr;`` statements."""
    scan = strip_comments(text)
    out: List[ContAssign] = []
    for m in _ASSIGN_RE.finditer(scan):
        prefix = m.group("prefix")
        out.append(ContAssign(
            lhs=m.group("lhs").strip(),
            rhs=text[m.start("rhs"):m.end("rhs")].strip(),
            start=m.start(),
            end=m.end(),
            line=scan.count("\n", 0, m.start()) + 1,
            decl_prefix="" if prefix.strip().startswith("assign") else prefix,
        ))
    return out


_NONBLOCK_RE = re.compile(
    r"(?P<lhs>[A-Za-z_][\w$]*(?:\s*\[[^;]*?\])?)\s*<=\s*(?P<rhs>[^;]+?);", re.DOTALL)


def find_nonblocking(text: str) -> List[ContAssign]:
    scan = strip_comments(text)
    out: List[ContAssign] = []
    for m in _NONBLOCK_RE.finditer(scan):
        out.append(ContAssign(
            lhs=m.group("lhs").strip(),
            rhs=text[m.start("rhs"):m.end("rhs")].strip(),
            start=m.start(), end=m.end(),
            line=scan.count("\n", 0, m.start()) + 1,
        ))
    return out


_LOCALPARAM_RE = re.compile(
    r"\blocalparam\b(?P<type>[^;=]*?)?(?P<body>[A-Za-z_][\w$]*\s*=[^;]+);", re.DOTALL)
_PARAM_ITEM_RE = re.compile(r"([A-Za-z_][\w$]*)\s*=\s*([^,;]+)")


def localparams(text: str) -> Dict[str, str]:
    """Name -> literal value for every ``localparam`` in ``text``."""
    scan = strip_comments(text)
    out: Dict[str, str] = {}
    for m in _LOCALPARAM_RE.finditer(scan):
        for name, value in _PARAM_ITEM_RE.findall(m.group("body")):
            out[name] = value.strip()
    return out


_NUM_RE = re.compile(r"^(?:(\d+)\s*'\s*([bodhBODH])\s*)?([0-9a-fA-F_]+)$")


def eval_literal(tok: str, symbols: Optional[Dict[str, str]] = None,
                 _depth: int = 0) -> Optional[int]:
    """Evaluate a Verilog integer literal, following localparam names."""
    t = tok.strip()
    if symbols and t in symbols and _depth < 8:
        return eval_literal(symbols[t], symbols, _depth + 1)
    m = _NUM_RE.match(t.replace(" ", ""))
    if not m:
        return None
    base = (m.group(2) or "d").lower()
    digits = m.group(3).replace("_", "")
    try:
        return int(digits, {"b": 2, "o": 8, "d": 10, "h": 16}[base])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Ternary chain parsing
# ---------------------------------------------------------------------------
@dataclass
class TernaryChain:
    sel: str
    cases: List[Tuple[int, str]]     # (compare value, result expression)
    default: str
    ok: bool
    reason: str = ""


_COND_RE = re.compile(r"^\(?\s*(?P<sel>[A-Za-z_][\w$]*(?:\s*\[[^\]]*\])?)\s*==\s*"
                      r"(?P<val>[^)]+?)\s*\)?$")


def parse_ternary_chain(expr: str, symbols: Optional[Dict[str, str]] = None
                        ) -> TernaryChain:
    """Parse ``(s == A) ? x : (s == B) ? y : ... : z`` into a case table.

    Comments embedded in a multi-line expression are stripped first so that an
    inline ``// ...`` between cases never breaks the parse. Only the parsed
    case table is returned, so dropping the comments is safe.
    """
    cases: List[Tuple[int, str]] = []
    sel_name: Optional[str] = None
    cur = strip_comments(expr, keep_len=False).strip()
    while True:
        q = _find_top_level(cur, "?")
        if q < 0:
            break
        colon = _find_matching_colon(cur, q)
        if colon < 0:
            return TernaryChain("", [], "", False, "unbalanced ?: expression")
        cond = cur[:q].strip()
        then = cur[q + 1:colon].strip()
        rest = cur[colon + 1:].strip()
        m = _COND_RE.match(cond)
        if not m:
            return TernaryChain("", [], "", False,
                                f"condition is not a simple equality: {cond[:40]!r}")
        sel = m.group("sel").strip()
        if sel_name is None:
            sel_name = sel
        elif sel != sel_name:
            return TernaryChain("", [], "", False,
                                "chain compares more than one select signal")
        val = eval_literal(m.group("val"), symbols)
        if val is None:
            return TernaryChain("", [], "", False,
                                f"compare value {m.group('val')!r} is not a constant")
        cases.append((val, then))
        cur = rest
    if sel_name is None or len(cases) < 3:
        return TernaryChain("", [], "", False, "not a ternary chain of >=3 cases")
    return TernaryChain(sel_name, cases, cur.strip(), True)


def _find_top_level(expr: str, ch: str) -> int:
    depth = 0
    for i, c in enumerate(expr):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == ch:
            return i
    return -1


def _find_matching_colon(expr: str, q: int) -> int:
    """Index of the ':' that pairs with the '?' at ``q``."""
    depth = 0
    pending = 0
    i = q + 1
    while i < len(expr):
        c = expr[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "?":
            pending += 1
        elif depth == 0 and c == ":":
            # ignore ':' inside part-selects (already covered by depth) and
            # inside 'a +: b' style ranges (also depth-guarded)
            if pending == 0:
                return i
            pending -= 1
        i += 1
    return -1


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def line_span(text: str, start: int, end: int) -> Tuple[int, int]:
    return text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1


def indent_of(text: str, offset: int) -> str:
    bol = text.rfind("\n", 0, offset) + 1
    seg = text[bol:offset]
    return seg[: len(seg) - len(seg.lstrip())]


def module_body(text: str, module: str) -> Optional[Tuple[int, int]]:
    """Character span of ``module <name> ... endmodule``."""
    scan = strip_comments(text)
    m = re.search(rf"\bmodule\s+{re.escape(module)}\b", scan)
    if not m:
        return None
    e = scan.find("endmodule", m.end())
    if e < 0:
        return None
    return m.start(), e + len("endmodule")


def annotation(text: str, key: str) -> Optional[str]:
    """Read a ``// genrtl-<key>: value`` annotation from the file header."""
    m = re.search(rf"genrtl[-_]{re.escape(key)}\s*[:=]\s*(?P<v>[^\n\r*]+)", text)
    return m.group("v").strip() if m else None
