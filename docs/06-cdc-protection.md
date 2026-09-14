# 06 — CDC protection: the do-not-touch set

The benchmark contains synchronisers, asynchronous FIFOs, a handshake, a pulse
synchroniser and a clock generator. These are the structures where an
"optimisation" is most likely to be catastrophic *and* least likely to be caught
— because most of them survive equivalence checking.

Retiming one flop out of a two-flop synchroniser leaves the combinational
function unchanged. The design still simulates correctly. It still passes
equivalence checking. What it no longer has is the metastability settling time
that made the crossing legal in the first place, and the failure shows up as an
intermittent silicon bug.

So CDC logic is not protected by the formal gate. It is protected by never being
proposed.

## Four independent detectors

`genrtl/protect.py` runs all four. A module is protected if **any** of them
fires, and every reason is recorded.

### 1. Path

The file lives under `rtl/cdc/` or `rtl/clk/`.

### 2. Name

The module name matches a known CDC or clock-generation pattern: `^cdc_`,
`_cdc$`, `sync_\d*ff$`, `_sync$`, `_synchron`, `async_fifo`, `_fifo$`,
`pulse_sync`, `handshake`, `clkgen`, `clk_gen`, `clk_div`, `_reset_sync`.

### 3. Annotation

The RTL carries a marker in its header, either as a comment or an attribute:

```systemverilog
// genrtl-protect: cdc_synchronizer
(* genrtl_protect = "cdc_synchronizer" *)
(* keep_hierarchy *)
module cdc_sync_2ff #(...)
```

### 4. Structural

The first three all depend on someone having followed a convention. The fourth
does not: it walks the elaborated netlist looking for a chain of two or more
flops where

- flop B's D is driven directly by flop A's Q,
- the head of the chain takes its D straight from a module input,
- every flop in the chain is enable-free, and
- each intermediate flop's Q feeds **only** the next flop in the chain.

That is the shape of a synchroniser regardless of what it is called or where it
lives.

The last two conditions are not decoration. A valid-delay flop chain running
from a module input to a module output is structurally identical to a two-flop
synchroniser — a pipelined MAC has exactly that shape. What separates them is
that the intermediate flop of a pipeline also gates the datapath, and a real
synchroniser's never does. Without the fanout condition, pipelining a module
once would make the optimiser refuse to touch it ever again. The regression
suite pins this: `test_datapath_modules_are_not_protected` runs against a
netlist that has *already* been pipelined.

On this benchmark it independently identifies `cdc_sync_2ff`, reporting both the
2-flop and the 3-flop instantiations:

```
cdc_sync_2ff  ['path: file lives under rtl/cdc',
               'name: matches /^cdc_/',
               'annotation: genrtl-protect marker in file header',
               'structural: 3-flop synchroniser chain fed from a module input',
               'structural: 2-flop synchroniser chain fed from a module input']
```

Four detectors agreeing is the point. Any one of them can be defeated by a
design that does not follow the convention; all four failing at once takes
effort.

## What gets protected on this benchmark

```
cdc_async_fifo   path, name, annotation
cdc_handshake    path, name, annotation
cdc_pulse_sync   path, name, annotation
cdc_sync_2ff     path, name, annotation, structural (2-flop and 3-flop chains)
nebula_clkgen    path, name, annotation
```

## Where protection is enforced

Twice, deliberately.

**During localisation.** A protected module is never selected as the transform
target, and when *every* module on a path is protected the path is skipped with
the reason logged:

```
[03] skip u_fifo_i2s/_6795_/D -- every module on this path is protected:
     cdc_async_fifo
```

**During patch validation.** Before any other gate runs, the file the patch
would modify is checked against the protected set. This is belt-and-braces: it
catches a proposal that reached the transform layer some other way — an LLM
naming a module directly, a hand-written site, a future transform whose site
selection has a bug.

## Adding your own

```bash
# extend the pattern list
edit genrtl/protect.py    # PROTECTED_NAME_PATTERNS / PROTECTED_DIRS

# or mark a specific module in the RTL
// genrtl-protect: my_reason
```

`build(nl_elab, rtl_root, extra_modules=[...])` also accepts an explicit list.

## What this does not do

It does not verify that the CDC structures are *correct*. It guarantees the
optimiser leaves them exactly as it found them. Checking that a crossing is
properly synchronised in the first place is a CDC lint problem, and a real flow
would run a dedicated CDC tool alongside this one.
