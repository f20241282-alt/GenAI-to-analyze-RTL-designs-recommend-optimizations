# 09 — Limitations

Stating these plainly is more useful than overselling the system.

## The flow is synthesis-only

There is no placement, no routing, no parasitic extraction. Delays come from
Yosys technology mapping plus OpenSTA wire-load estimates. Two consequences:

- **Absolute delays are pessimistic.** A 32-bit ALU path measures around 9 ns on
  Nangate45 here, where a commercial flow with sizing and buffering would give a
  small fraction of that. Yosys and ABC do delay-driven *mapping* but no gate
  sizing and no buffer insertion.
- **Relative improvements are the meaningful number.** The baseline and every
  optimised version go through exactly the same flow, so the before/after deltas
  are a fair comparison of what the RTL rewrite bought. Treat the absolute
  frequency numbers as benchmark-internal.

Adding OpenROAD for placement would tighten this considerably and is the obvious
next step.

## One global ABC delay target

`abc -D <ps>` takes a single target, but the design has ten clocks with periods
from 1.45 ns to 42 ns. The tightest master period is used. A production flow
would run per-clock-domain mapping or a physical-aware flow instead.

## Bounded proofs are bounded

Latency-changing transformations and encoding-changing transformations are proved
by bounded model checking, not by induction. The depth is chosen from a fixed
cell×cycle budget and is **reported with every proof**:

```
bounded equivalence proven to depth 8 with a 1-cycle latency offset
```

Depth 8 on a two-round AES pipeline covers reset, fill, steady state and several
back-to-back transactions, which is a strong bound — but it is not a proof for
all time. `equiv_induct`-style unbounded proofs for these cases would need
invariants the flow does not currently generate.

Latency- and encoding-preserving transformations (tree balancing, Boolean
factoring, register duplication) *are* proved unbounded, by temporal induction.

## Stability contracts are assumptions

Pipelining `aes_round` is only equivalent if `rkey0` and `rkey1` are stable while
a block is in flight. The RTL declares this with `genrtl-latency-stable` and the
miter turns it into an explicit `assume`. That is honest — the assumption appears
in the proof result and in the report — but it *is* an assumption. Something
outside the module has to guarantee it. Here `nebula_top` drives both keys from a
static primary input, which does; a different integration might not, and nothing
in this flow checks the integration.

The same applies to `genrtl-latency-flex`. The optimiser trusts the RTL's claim
that its consumer tolerates extra latency. Verifying that claim at the top level
is a system-level property, not a module-level one.

## Coverage of the transform catalogue

Eight transformations cover a useful slice of real timing-closure work, but only
a slice. Not implemented: operator strength reduction, carry-select or
carry-lookahead adder substitution, clock gating restructuring, memory banking,
retiming across module boundaries, or anything requiring a floorplan.

When nothing in the catalogue matches, the loop says so and moves on rather than
guessing:

```
[04] no proposal for g_crc[0].u_crc/_813_/D -- no catalogued transformation
     matches this path
```

That is the correct behaviour, and it is also a measure of catalogue coverage:
the ratio of "no proposal" iterations to total iterations tells you how much of
the design the library can actually reach.

## The RTL parser is lexical, not a full parser

`genrtl/rtlparse.py` is comment- and bracket-aware but is not a SystemVerilog
front end. Transform preconditions are written to be conservative about what
they will match — the `balanced_adder_tree` transform refuses terms that are not
simple operands, `pipeline_insert` refuses wire chains inside generate blocks,
`resource_duplication` refuses registers driven by more than one block. A parser
built on a real front end (Slang, Verible) would widen coverage. The verification
gate is the backstop, but a good precondition is worth more than a backstop.

## Sequential equivalence scope

Equivalence is proved at the level of the **changed module**, not the whole
design, and at every parameterisation the design instantiates. The end-of-run
check re-proves each changed module against the untouched original. Whole-design
equivalence after all patches is not run, because for a 50 K-cell design with a
pipelined submodule it needs a system-level latency model that the flow does not
build.

## The optimiser does not close timing

On this benchmark it removes a large share of the violation — around 74 % of
WNS and 90 % of TNS on the full configuration — but it does not reach positive
slack in every domain. Some remaining paths sit entirely inside protected CDC
logic, where the correct action is a constraint or architecture change by a
human. Others need transformations the catalogue does not have.

A system that claimed to always close timing would be lying. This one reports
exactly what it achieved and why it stopped.

## What has not been tested at scale

Everything here runs on one benchmark. The transform preconditions, the
localisation heuristics and the diagnosis classifier have not been validated
against a broad corpus of third-party RTL, and some of them will need widening
before they generalise.
