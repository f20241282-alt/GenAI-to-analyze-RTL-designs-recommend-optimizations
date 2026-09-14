// RTL file list for the Nebula timing-closure benchmark.
// Order matters for Yosys: leaf modules first, top last.
rtl/clk/nebula_clkgen.sv
rtl/cdc/cdc_sync_2ff.sv
rtl/cdc/cdc_pulse_sync.sv
rtl/cdc/cdc_handshake.sv
rtl/cdc/cdc_async_fifo.sv
rtl/dsp/dsp_adder_chain.sv
rtl/dsp/dsp_mux_chain.sv
rtl/dsp/dsp_mac.sv
rtl/dsp/dsp_lane.sv
rtl/crypto/aes_sbox.sv
rtl/crypto/aes_round.sv
rtl/crc/crc32_par.sv
rtl/core/rv_alu.sv
rtl/core/rv_regfile.sv
rtl/core/rv_lane.sv
rtl/ctrl/ctrl_fsm.sv
rtl/ctrl/fanout_hub.sv
rtl/nebula_top.sv
