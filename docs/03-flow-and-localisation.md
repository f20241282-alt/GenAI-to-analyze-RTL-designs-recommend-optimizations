# 03 — Flow and STA-driven RTL localisation

## The synthesis script

`genrtl/synth.py` generates one Yosys script per run:

```tcl
read_verilog -sv -D<CFG> -I rtl <files...>
hierarchy -check -top nebula_top
proc
opt_clean
write_json elab.json          # RTL-level structure, rich src annotations

synth -nofsm -top nebula_top
dfflibmap -liberty <lib>
abc -liberty <lib> -D <target_ps>
setundef -zero
opt_clean -purge

write_json mapped.json        # technology-mapped netlist
write_verilog -noattr netlist.v
tee -o stat.txt stat -liberty <lib> -top nebula_top
```

Three details matter.

**`-nofsm`.** Yosys is explicitly forbidden from re-encoding the FSM itself. If
it were allowed to, any improvement from the `fsm_reencode` transformation would
be the synthesiser's, not the optimiser's, and the measurement would be
meaningless.

**Hierarchy is preserved.** `synth` is not run with `-flatten`, so instance
paths in the netlist still correspond to RTL instances. That is what makes
localisation possible, and it is why the SDC can attach generated clocks to
`u_clkgen/clk_core_div2` instead of to a post-ABC cell name that changes every
run.

**Two JSON dumps.** `elab.json` is written before optimisation, while every cell
still carries the `src` attribute Yosys derives from the source. `mapped.json`
is written after technology mapping. Both are needed, because ABC destroys `src`
on combinational cells (33 of 1799 cells in `rv_alu` keep it) but preserves it on
registers and on modules.

## Static timing analysis

`genrtl/sta.py` generates an OpenSTA Tcl script that reads the slow corner for
setup and the fast corner for hold, links the design, applies the SDC, and then
writes a **machine-readable JSON** rather than a text report:

```tcl
read_liberty -max Nangate45_slow.lib
read_liberty -min Nangate45_fast.lib
read_verilog netlist.v
link_design nebula_top
read_sdc constraints/nebula.sdc

set setup_ends [find_timing_paths -path_delay max -group_path_count 24 \
                  -endpoint_path_count 1 -sort_by_slack -unique_paths_to_endpoint]
```

For each path end the script pulls `startpoint`, `endpoint`, `startpoint_clock`,
`endpoint_clock`, `slack`, and the full list of path points with `arrival`,
`required` and `slack` at each. Text `report_checks` output is also written for
human reading, but nothing in the flow parses it.

`-group_path_count` is per path group, and path groups default to clocks, so a
setting of 24 gives up to 24 worst paths *per clock domain*, not 24 overall.
That matters: without it the whole budget would be spent on whichever domain is
worst, and the other five violating domains would never be seen.

## Localisation

This is the piece that makes a 50 K-cell design tractable. `genrtl/localize.py`
turns one OpenSTA path into a small slice of the original SystemVerilog.

### Step 1 — resolve every path point to its owning module

An OpenSTA pin name is a hierarchical path:

```
g_rv[0].u_lane/u_alu/_3565_/D
```

`genrtl/netlist.py` walks it against `mapped.json`: `g_rv[0].u_lane` is a cell
of type `$paramod\rv_lane\LANE_ID=...`, inside which `u_alu` is a cell of type
`rv_alu`, inside which `_3565_` is a leaf standard cell. So the point belongs to
instance `g_rv[0].u_lane/u_alu`, RTL module `rv_alu`.

`$paramod` decoration is stripped to recover the RTL name, and the parameter
values encoded in it are recovered too — they are what lets the formal gate
prove the module at the parameterisation the design actually uses, and what lets
a transform decline a site that only exists at some other parameterisation.

### Step 2 — attribute delay to modules

Each path point carries an arrival time, so the incremental delay of each stage
is the difference from the previous point. Summing those per instance gives a
delay-by-module breakdown:

```
delay_by_module = {"aes_sbox": 5.463, "aes_round": 5.326, "nebula_top": 0.0}
```

That breakdown goes into the prompt. It is the difference between "this path is
slow" and "76 % of this path is inside these two modules".

### Step 3 — choose the module to transform

The module that owns the **capturing register** wins by default: retiming,
pipelining and output-mux restructuring all have to happen where the capturing
flop lives. If that module is protected or has no resolvable source, the fallback
is the unprotected module carrying the most delay. Modules carrying less than 2 %
of the path delay are dropped — the path may run through them, but there is
nothing there worth changing.

### Step 4 — cut the slice

The chosen module's `src` attribute gives a file and a line span. If that span
is under 120 lines it is used directly. If the module is larger, the slice is
narrowed to ±25 lines around the endpoint register's own `src` attribute — which
survives technology mapping precisely because `dfflibmap` preserves attributes
on flops.

The result for a typical path:

```
[clk_sec] slack=-7.726 depth=58
   primary   = aes_round
   file      = rtl/crypto/aes_round.sv:17-89
   bottleneck= logic_depth
   delay_by_module = {aes_sbox: 5.463, aes_round: 5.326}
```

73 lines of RTL instead of 50 000 cells.

### Step 5 — diagnose

A path is classified before anything is proposed:

| Diagnosis | Condition | What it means |
|---|---|---|
| `single_dominant_stage` | one stage is over 35 % of the path delay | a driver or load problem |
| `logic_depth` | 40 or more timing points | too many levels of logic |
| `moderate_depth` | 12–39 timing points | some restructuring may help |
| `load_or_fanout` | fewer than 12 points but still failing | capacitance, not depth |

The diagnosis gates which transformations are even offered. Duplicating a
register does nothing for a path that is simply too deep, and rebalancing a tree
does nothing for a path dominated by one slow driver, so neither is proposed
where it cannot help.

## Blocked paths

When every module on a path is protected, localisation returns `blocked` with
the reason, and the loop moves on:

```
[03] skip u_fifo_i2s/_6795_/D -- every module on this path is protected:
     cdc_async_fifo
```

This happens in the full benchmark and it is the correct outcome, not a
shortcoming: a violating path inside an asynchronous FIFO's pointer logic is a
constraint or architecture problem for a human, not something an automatic
rewriter should touch.
