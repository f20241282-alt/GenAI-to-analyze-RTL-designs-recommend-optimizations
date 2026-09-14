// ---------------------------------------------------------------------------
// nebula_defines.svh -- global compile-time configuration for the benchmark
//
// Two size configurations are supported:
//   NEBULA_CFG_SMALL : fast-iteration configuration
//   NEBULA_CFG_FULL  : ~50 K standard cell benchmark, per the project spec
//
// Select with  +define+NEBULA_CFG_FULL  (default is SMALL). Only the *lane
// counts* change between configurations -- every RTL construct, every clock
// domain and every CDC structure is present in both, so a transform validated
// on SMALL is valid on FULL.
// ---------------------------------------------------------------------------
`ifndef NEBULA_DEFINES_SVH
`define NEBULA_DEFINES_SVH

`ifdef NEBULA_CFG_FULL
  `define NEBULA_DSP_LANES    4
  `define NEBULA_AES_LANES    1
  `define NEBULA_AES_ROUNDS   2
  `define NEBULA_CRC_LANES    3
  `define NEBULA_RV_LANES     3
  `define NEBULA_FIFO_AW      3
`else
  `define NEBULA_DSP_LANES    1
  `define NEBULA_AES_LANES    1
  `define NEBULA_AES_ROUNDS   1
  `define NEBULA_CRC_LANES    1
  `define NEBULA_RV_LANES     1
  `define NEBULA_FIFO_AW      3
`endif

`endif // NEBULA_DEFINES_SVH
