"""Streamlit dashboard for a GenRTL optimisation run.

    pip install streamlit plotly pandas
    streamlit run dashboard/app.py

Point it at any directory under ``runs/`` that contains ``run.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "runs"
sys.path.insert(0, str(REPO))
from genrtl import advisor as _advisor          # noqa: E402

# Palette: one accent for "before", one for "after", one for rejected.
C_BEFORE = "#8C8C8C"
C_AFTER = "#2E6FDB"
C_GOOD = "#1B9E77"
C_BAD = "#D1495B"
C_GRID = "rgba(128,128,128,0.22)"

st.set_page_config(page_title="GenRTL timing closure", layout="wide")


def _runs():
    if not RUNS.is_dir():
        return []
    return sorted([p for p in RUNS.iterdir() if (p / "run.json").is_file()],
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _style(fig, height=340):
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=36, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(size=13), legend=dict(orientation="h", y=1.12, x=0),
    )
    fig.update_xaxes(gridcolor=C_GRID, zerolinecolor=C_GRID)
    fig.update_yaxes(gridcolor=C_GRID, zerolinecolor=C_GRID)
    return fig


# ---------------------------------------------------------------------------
_SEV_COLOR = {"high": "#D1495B", "medium": "#E9A23B", "low": "#8C8C8C",
              "info": "#8C8C8C"}


def advisor_page():
    """Standalone 'suggest optimisations for any RTL' page -- no toolchain."""
    st.title("Suggest optimisations for any RTL")
    st.caption("Pure-Python analysis — no Yosys or OpenSTA required. Paste RTL "
               "or pick a file; findings marked **AUTO** are rewrites the "
               "verified engine can apply and formally prove equivalent.")

    picks = {"— paste your own —": ""}
    for d in (REPO / "examples", REPO / "rtl"):
        if d.is_dir():
            for f in sorted(d.rglob("*.sv")):
                picks[str(f.relative_to(REPO)).replace("\\", "/")] = str(f)

    with st.sidebar:
        choice = st.selectbox("RTL source", list(picks), index=1 if len(picks) > 1 else 0)
        min_sev = st.select_slider("minimum severity",
                                   ["low", "medium", "high"], value="low")
        auto_only = st.checkbox("auto-appliable only", value=False)

    default_text = ""
    if picks.get(choice):
        default_text = Path(picks[choice]).read_text(errors="replace")
    src = st.text_area("RTL", value=default_text, height=280,
                       placeholder="module foo(...); ... endmodule")

    if not src.strip():
        st.info("Paste RTL above or choose a file from the sidebar.")
        return

    findings = _advisor.analyze_text(src, choice if picks.get(choice) else "<pasted>")
    rank = {"high": 3, "medium": 2, "low": 1, "info": 0}
    findings = [f for f in findings if rank[f.severity] >= rank[min_sev]]
    if auto_only:
        findings = [f for f in findings if f.auto]

    c = _advisor.summary_counts(findings)
    m = st.columns(4)
    m[0].metric("findings", len(findings))
    m[1].metric("auto-appliable", c["auto"])
    m[2].metric("advisory", c["advisory"])
    m[3].metric("high severity", c["high"])

    if not findings:
        st.success("No optimisation opportunities found at this severity.")
        return

    df = pd.DataFrame([{
        "sev": f.severity, "kind": "AUTO" if f.auto else "advisory",
        "module": f.module, "line": f.line, "category": f.category,
        "finding": f.title,
    } for f in findings])
    st.dataframe(
        df.style.map(lambda v: f"color:{_SEV_COLOR.get(v, '')};font-weight:600",
                     subset=["sev"]),
        use_container_width=True, hide_index=True)

    st.subheader("Details")
    for f in findings:
        tag = "🟢 AUTO" if f.auto else "🔹 advisory"
        with st.expander(f"{tag} · {f.module}:{f.line} · {f.category} "
                         f"({f.severity}) — {f.title}"):
            if f.snippet:
                st.code(f.snippet, language="systemverilog")
            st.markdown(f"**Why:** {f.rationale}")
            st.markdown(f"**Fix:** {f.suggestion}")
            if f.benefit:
                st.markdown(f"**Expected benefit:** {f.benefit}")
            if f.auto:
                st.markdown(f"**Verified transform:** `{f.transform}` "
                            f"(proof: `{f.proof}`) — params "
                            f"`{f.params}`")
    st.download_button("download findings (JSON)", _advisor.to_json(findings),
                       file_name="advisor_findings.json", mime="application/json")


# ---------------------------------------------------------------------------
runs = _runs()

with st.sidebar:
    st.header("Mode")
    mode = st.radio("view", ["Optimisation run", "Analyze any RTL"],
                    label_visibility="collapsed")

if mode == "Analyze any RTL":
    advisor_page()
    st.stop()

if not runs:
    st.title("GenRTL timing closure")
    st.warning("No completed runs found under `runs/`. "
               "Run `python -m genrtl --bench small optimize --name opt` first, "
               "or switch to **Analyze any RTL** in the sidebar (no toolchain "
               "needed).")
    st.stop()

with st.sidebar:
    st.header("Run")
    names = [p.name for p in runs]
    pick = st.selectbox("select", names, index=0)
    run_dir = RUNS / pick
    run = json.loads((run_dir / "run.json").read_text())
    mpath = run_dir / "metrics.json"
    m = json.loads(mpath.read_text()) if mpath.is_file() else {}
    cfg = run.get("config", {})
    st.caption(f"benchmark **{cfg.get('bench')}** · platform "
               f"**{cfg.get('platform')}** · engine **{cfg.get('backend')}**")
    st.caption(f"{run.get('seconds', 0):.0f} s · "
               f"{len(run.get('iterations', []))} iterations")

base, fin = run.get("baseline", {}), run.get("final", {})

st.title("GenAI-assisted RTL timing closure")
st.caption("Every number below comes from Yosys, OpenSTA and the Yosys formal "
           "engine — nothing here is estimated.")

# ---- headline ------------------------------------------------------------
c = st.columns(5)
c[0].metric("WNS (ns)", f"{fin.get('wns_ns', 0):.3f}",
            f"{fin.get('wns_ns', 0) - base.get('wns_ns', 0):+.3f}")
c[1].metric("TNS (ns)", f"{fin.get('tns_ns', 0):.1f}",
            f"{fin.get('tns_ns', 0) - base.get('tns_ns', 0):+.1f}")
c[2].metric("Setup violations", fin.get("setup_violations", 0),
            f"{fin.get('setup_violations', 0) - base.get('setup_violations', 0):+d}",
            delta_color="inverse")
c[3].metric("Worst hold (ns)", f"{fin.get('whs_ns', 0):.4f}",
            f"{fin.get('whs_ns', 0) - base.get('whs_ns', 0):+.4f}")
area_pct = ((fin.get("area", 0) - base.get("area", 1)) /
            max(base.get("area", 1), 1) * 100)
c[4].metric("Cell area", f"{fin.get('area', 0):,.0f}", f"{area_pct:+.2f}%",
            delta_color="inverse")

tabs = st.tabs(["Timing", "Iterations", "Verification", "Protected logic",
                "Patches"])

# ---- timing ---------------------------------------------------------------
with tabs[0]:
    left, right = st.columns(2)

    prog = [{"iteration": 0, "wns": base.get("wns_ns", 0),
             "tns": base.get("tns_ns", 0)}]
    for it in run.get("iterations", []):
        ma = it.get("metrics_after") or {}
        if it.get("accepted") and ma:
            prog.append({"iteration": it["iteration"], "wns": ma.get("wns_ns", 0),
                         "tns": ma.get("tns_ns", 0)})
    dfp = pd.DataFrame(prog)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dfp["iteration"], y=dfp["wns"], mode="lines+markers",
                             name="WNS (ns)", line=dict(color=C_AFTER, width=3)))
    fig.add_hline(y=0, line_dash="dot", line_color=C_GOOD)
    fig.update_layout(title="Worst negative slack per accepted patch",
                      xaxis_title="iteration", yaxis_title="WNS (ns)")
    left.plotly_chart(_style(fig), use_container_width=True)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dfp["iteration"], y=dfp["tns"], mode="lines+markers",
                             name="TNS (ns)", line=dict(color=C_AFTER, width=3),
                             fill="tozeroy", fillcolor="rgba(46,111,219,0.15)"))
    fig.update_layout(title="Total negative slack",
                      xaxis_title="iteration", yaxis_title="TNS (ns)")
    right.plotly_chart(_style(fig), use_container_width=True)

    bc, fc = base.get("per_clock", {}), fin.get("per_clock", {})
    clocks = sorted(set(bc) | set(fc))
    fig = go.Figure()
    fig.add_trace(go.Bar(x=clocks, y=[bc.get(k, {}).get("wns", 0) for k in clocks],
                         name="before", marker_color=C_BEFORE))
    fig.add_trace(go.Bar(x=clocks, y=[fc.get(k, {}).get("wns", 0) for k in clocks],
                         name="after", marker_color=C_AFTER))
    fig.add_hline(y=0, line_dash="dot", line_color=C_GOOD)
    fig.update_layout(title="WNS per clock domain", barmode="group",
                      yaxis_title="WNS (ns)")
    st.plotly_chart(_style(fig, 380), use_container_width=True)

    bf, ff = base.get("fmax_mhz", {}), fin.get("fmax_mhz", {})
    fig = go.Figure()
    fig.add_trace(go.Bar(x=clocks, y=[bf.get(k, 0) for k in clocks],
                         name="before", marker_color=C_BEFORE))
    fig.add_trace(go.Bar(x=clocks, y=[ff.get(k, 0) for k in clocks],
                         name="after", marker_color=C_AFTER))
    fig.update_layout(title="Achievable frequency per clock domain",
                      barmode="group", yaxis_title="MHz")
    st.plotly_chart(_style(fig, 380), use_container_width=True)

# ---- iterations -----------------------------------------------------------
with tabs[1]:
    rows = []
    for it in run.get("iterations", []):
        pr, p = it.get("proposal", {}), it.get("path", {})
        rows.append({
            "iter": it["iteration"],
            "clock": p.get("end_clock", ""),
            "slack in": round(p.get("slack", 0), 4) if p else None,
            "transform": pr.get("transform") or "—",
            "module": pr.get("module") or "—",
            "diagnosis": it.get("localization", {}).get("bottleneck", ""),
            "verdict": "accepted" if it.get("accepted") else "rejected",
            "why": it.get("reason", "")[:110],
            "s": round(it.get("seconds", 0), 1),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        st.dataframe(
            df.style.map(
                lambda v: (f"color:{C_GOOD};font-weight:600" if v == "accepted"
                           else f"color:{C_BAD}" if v == "rejected" else ""),
                subset=["verdict"]),
            use_container_width=True, hide_index=True)

    if m.get("by_transform"):
        bt = pd.DataFrame([
            {"transform": k, "proposed": v["proposed"], "accepted": v["accepted"]}
            for k, v in m["by_transform"].items()]).sort_values("proposed",
                                                               ascending=False)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=bt["transform"], y=bt["proposed"], name="proposed",
                             marker_color=C_BEFORE))
        fig.add_trace(go.Bar(x=bt["transform"], y=bt["accepted"], name="accepted",
                             marker_color=C_GOOD))
        fig.update_layout(title="Transformations proposed and accepted",
                          barmode="overlay")
        fig.update_traces(opacity=0.9)
        st.plotly_chart(_style(fig, 340), use_container_width=True)

    sel = st.selectbox("inspect an iteration",
                       [r["iter"] for r in rows] or [0])
    it = next((i for i in run.get("iterations", []) if i["iteration"] == sel), None)
    if it:
        a, b = st.columns([1, 1])
        loc = it.get("localization", {})
        a.markdown("**Failing path**")
        a.code(f"start : {it.get('path', {}).get('startpoint')}\n"
               f"end   : {it.get('path', {}).get('endpoint')}\n"
               f"clock : {it.get('path', {}).get('end_clock')}\n"
               f"slack : {it.get('path', {}).get('slack', 0):+.4f} ns\n"
               f"depth : {it.get('path', {}).get('depth')}\n"
               f"module: {loc.get('primary_module')}\n"
               f"source: {loc.get('slice_file')}:{loc.get('slice_lines')}",
               language="text")
        b.markdown("**Proposal**")
        b.write(it.get("proposal", {}).get("explanation", ""))
        b.json(it.get("proposal", {}).get("params") or {})
        st.markdown("**Gates**")
        st.dataframe(pd.DataFrame(it.get("gates", [])), use_container_width=True,
                     hide_index=True)
        if it.get("diff"):
            st.markdown("**Patch**")
            st.code(it["diff"], language="diff")

# ---- verification ---------------------------------------------------------
with tabs[2]:
    st.subheader("Why a patch was rejected")
    rr = m.get("rejection_reasons", {})
    if rr:
        fig = go.Figure(go.Bar(x=list(rr.keys()), y=list(rr.values()),
                               marker_color=C_BAD))
        fig.update_layout(title="Rejections by gate")
        st.plotly_chart(_style(fig, 300), use_container_width=True)
    else:
        st.info("No patch was rejected in this run.")

    st.subheader("Formal proofs")
    proofs = [{**p, "iteration": it["iteration"]}
              for it in run.get("iterations", []) for p in it.get("proofs", [])]
    if proofs:
        st.dataframe(pd.DataFrame(proofs)[
            ["iteration", "module", "engine", "latency", "bmc_depth", "passed",
             "detail", "seconds"]], use_container_width=True, hide_index=True)

    st.subheader("End-to-end equivalence against the original RTL")
    fe = run.get("final_equivalence", [])
    if fe:
        st.dataframe(pd.DataFrame(fe)[
            ["file", "module", "engine", "cumulative_latency", "passed", "detail"]],
            use_container_width=True, hide_index=True)
        if all(p.get("passed") for p in fe):
            st.success("Every changed module is proved equivalent to the "
                       "untouched original RTL.")
    else:
        st.info("No module changed, so there is nothing to re-prove.")

# ---- protection -----------------------------------------------------------
with tabs[3]:
    st.subheader("Do-not-touch set")
    st.caption("A module lands here if ANY detector fires. The structural "
               "detector is what catches an unlabelled synchroniser.")
    for p in run.get("protection", []):
        with st.expander(f"{p['module']}", expanded=False):
            for r in p["reasons"]:
                st.write(f"- {r}")
            if p.get("file"):
                st.code(p["file"], language="text")
    blocked = [it for it in run.get("iterations", [])
               if it.get("localization", {}).get("blocked")]
    if blocked:
        st.subheader("Paths the optimiser refused to touch")
        st.dataframe(pd.DataFrame([{
            "iteration": b["iteration"],
            "endpoint": b.get("path", {}).get("endpoint"),
            "slack": b.get("path", {}).get("slack"),
            "reason": b.get("localization", {}).get("blocked_reason"),
        } for b in blocked]), use_container_width=True, hide_index=True)

# ---- patches --------------------------------------------------------------
with tabs[4]:
    pd_dir = run_dir / "patches"
    files = sorted(pd_dir.glob("*.diff")) if pd_dir.is_dir() else []
    if not files:
        st.info("No patches were written for this run.")
    for f in files:
        with st.expander(f.name, expanded=f.name.endswith("accepted.diff")):
            st.code(f.read_text(), language="diff")
