"""Unit tests for the SystemVerilog lexical helpers.

Run with pytest, or `python3 tests/run_all.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genrtl import rtlparse as rp   # noqa: E402


def test_strip_comments_preserves_offsets():
    src = "wire a; // comment\nwire b; /* block */ wire c;\n"
    out = rp.strip_comments(src)
    assert len(out) == len(src)
    assert "comment" not in out and "block" not in out
    assert "wire c;" in out


def test_split_top_level_ignores_brackets():
    assert rp.split_top_level("a + b + c", "+") == ["a", "b", "c"]
    assert rp.split_top_level("f(a + b) + c", "+") == ["f(a + b)", "c"]
    assert rp.split_top_level("x[a+1] + y", "+") == ["x[a+1]", "y"]


def test_split_top_level_does_not_split_logical_ops():
    assert rp.split_top_level("a && b", "&") == ["a && b"]
    assert rp.split_top_level("a || b", "|") == ["a || b"]
    assert rp.split_top_level("a ^~ b", "^") == ["a ^~ b"]


def test_balanced_tree_depth():
    expr = rp.balanced_tree(["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"], "+")
    # eight leaves in a balanced tree means three levels of parentheses
    assert expr.count("(") == 7
    assert expr.startswith("((") and "a7" in expr


def test_localparams_and_literals():
    src = "localparam [3:0] A = 4'd0, B = 4'd7; localparam C = 8'hFF;"
    syms = rp.localparams(src)
    assert syms == {"A": "4'd0", "B": "4'd7", "C": "8'hFF"}
    assert rp.eval_literal("B", syms) == 7
    assert rp.eval_literal("8'hFF") == 255
    assert rp.eval_literal("3'b101") == 5
    assert rp.eval_literal("not_a_number") is None


def test_ternary_chain_parses_localparam_conditions():
    src = "localparam [1:0] X = 2'd0, Y = 2'd1, Z = 2'd2;"
    syms = rp.localparams(src)
    chain = rp.parse_ternary_chain(
        "(s == X) ? a : (s == Y) ? b : (s == Z) ? c : d", syms)
    assert chain.ok, chain.reason
    assert chain.sel == "s"
    assert [v for v, _ in chain.cases] == [0, 1, 2]
    assert chain.default == "d"


def test_ternary_chain_rejects_non_equality_conditions():
    chain = rp.parse_ternary_chain("(s > 1) ? a : (s == 2) ? b : (s == 3) ? c : d")
    assert not chain.ok


def test_find_assignments_handles_both_forms():
    src = """
    module m;
      wire [3:0] x = a + b;
      assign y = c ^ d;
    endmodule
    """
    got = {a.lhs: a.rhs for a in rp.find_assignments(src)}
    assert got["x"] == "a + b"
    assert got["y"] == "c ^ d"


def test_annotation():
    src = "// genrtl-latency-flex: 2\nmodule m; endmodule"
    assert rp.annotation(src, "latency-flex") == "2"
    assert rp.annotation(src, "missing") is None


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
