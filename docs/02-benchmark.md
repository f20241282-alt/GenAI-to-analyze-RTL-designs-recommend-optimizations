# 02 — The benchmark

The project brief calls for a design with *five independent asynchronous master
clocks, at least one generated clock per master, clock dividers with multiple
ratios, real CDC crossings, and roughly 50 K standard cells*. Anything smaller
lets naive approaches look good; anything without real CDC structures lets an
optimiser get away with edits that would be catastrophic on silicon.

`rtl/nebula_top.sv` is that design.

## Clock architecture

| Master clock | Period | Generated clock | Ratio | What lives there |
|---|---|---|---|---|
| `clk_core` | 7.50 ns | `clk_core_div2` | ÷2 | RV32-style lanes (regfile + operand pipeline + ALU), high-fanout control hub |
| `clk_dsp` | 6.00 ns | `clk_dsp_div3` | ÷3 | DSP lanes: 16:1 select → 8-term reduce → multiply-accumulate |
| `clk_mem` | 1.45 ns | `clk_mem_div4` | ÷4 | 12-state control sequencer |
| `clk_io` | 4.20 ns | `clk_io_div5` | ÷5 | Parallel CRC-32 engines |
| `clk_sec` | 5.00 ns | `clk_sec_div8` | ÷8 | AES round datapath (2 rounds per cycle in the full configuration) |

The five masters are declared asynchronous to one another with
`set_clock_groups -asynchronous`, so paths between them are not timed as
synchronous transfers — which is exactly why every crossing needs a real CDC
structure rather than a timing exception.

The dividers live in `rtl/clk/nebula_clkgen.sv`. Their output flops carry
`(* keep *)`, and the SDC hangs `create_generated_clock` on the module's output
pins (`u_clkgen/clk_core_div2` and friends), so the generated-clock definitions
survive synthesis without depending on post-ABC cell names.

## CDC structures

Every domain crossing in the design is a real structure, not a false path:

| Crossing | Structure | File |
|---|---|---|
| core → dsp | Gray-pointer asynchronous FIFO | `rtl/cdc/cdc_async_fifo.sv` |
| dsp → mem | four-phase req/ack handshake with held data | `rtl/cdc/cdc_handshake.sv` |
| mem → io | toggle pulse synchroniser | `rtl/cdc/cdc_pulse_sync.sv` |
| io → sec | Gray-pointer asynchronous FIFO | `rtl/cdc/cdc_async_fifo.sv` |
| sec → core | four-phase handshake (closes the ring) | `rtl/cdc/cdc_handshake.sv` |
| core_div2 → mem | two-flop synchroniser | `rtl/cdc/cdc_sync_2ff.sv` |

All of these are on the do-not-touch list — see [06](06-cdc-protection.md).

## Deliberate optimisation targets

The design is not artificially slow; it is realistically slow, in the specific
ways real RTL is slow. Each engine contains one recognisable anti-pattern:

| Module | Anti-pattern | Transform that fixes it |
|---|---|---|
| `dsp_adder_chain` | 8-term left-associative `+` chain, depth 7 | `balanced_adder_tree` |
| `dsp_mux_chain` | 16-way selection written as a 16-deep priority chain | `balanced_mux_tree` |
| `rv_alu` | 16-deep result-select chain sitting after the shifter | `balanced_mux_tree` |
| `dsp_mac` | multiply + accumulate + saturate in one cycle | `pipeline_insert` |
| `aes_round` | two full AES rounds unrolled into one cycle | `pipeline_insert` |
| `ctrl_fsm` | 12 states in 4 bits, so every guard is a comparator | `fsm_reencode` |
| `ctrl_fsm` | guard written as an expanded sum of products | `boolean_factor` |
| `fanout_hub` | one 2-bit control register driving 64 loads | `resource_duplication` |

`ctrl_fsm` deserves a note. Its `phase_q` output is *decoded* from the state
rather than being the raw state vector. That is what makes re-encoding a legal
internal change — and the difference is not cosmetic: an earlier version of the
benchmark assigned `phase_q <= state_c` directly, and the formal gate correctly
refused the one-hot patch because the module's observable output really did
change.

## Two size configurations

`rtl/nebula_defines.svh` selects lane counts. Every clock domain, every CDC
structure and every anti-pattern is present in both, so a transformation
validated on the small configuration is valid on the full one.

| | DSP lanes | RV lanes | CRC lanes | AES rounds/cycle | Cells | Synthesis |
|---|---|---|---|---|---|---|
| `small` | 1 | 1 | 1 | 1 | ~20.8 K | ~12 s |
| `full` | 4 | 3 | 3 | 2 | ~50.0 K | ~15 s |

## The constraints

`constraints/nebula.sdc` is deliberately tight. It was not guessed: the periods
were derived by first analysing the design with a 20 ns period on every clock to
measure the true worst path delay per domain, then setting each period below
that. The result is a baseline that violates setup timing in six domains while
staying closable — a design that misses by 40 ns teaches nothing.

The SDC also carries:

- `set_clock_groups -asynchronous` for the five islands
- setup/hold clock uncertainty and clock transition
- input delays declared against *every* clock that samples a multi-domain
  control port, with `-add_delay`
- output delays on all outputs, including `div_out` which mixes divided domains
- `set_false_path` from the asynchronous reset ports

## Baseline numbers (Nangate45, slow corner for setup, fast corner for hold)

```
full : 49 958 cells, area 82 682
       WNS -7.726 ns   TNS -1262.9 ns   worst hold +0.0055 ns   116 violating paths
small: 20 751 cells, area 36 077
       WNS -1.614 ns   TNS   -83.3 ns   worst hold +0.0055 ns    68 violating paths
```

Worst hold slack is only +5.5 ps, so the hold gate in the acceptance rule is not
decorative: a transformation that shortens a launch path carelessly really can
push the design into a hold violation, and gets rejected when it does.

## Building the benchmark yourself

The brief suggests assembling open-source blocks rather than writing a large
design from scratch. This benchmark takes the same shape — a small RISC-style
ALU and register file, an AES datapath, a CRC engine, arithmetic datapaths, an
async FIFO — but the blocks are written here rather than imported, for three
reasons: the anti-patterns have to be placed deliberately, the latency contracts
have to be annotated, and every file has to be small enough that localisation
produces a slice a human can read.
