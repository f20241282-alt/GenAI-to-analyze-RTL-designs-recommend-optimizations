# ---------------------------------------------------------------------------
# nebula.sdc -- timing constraints for the Nebula benchmark
#
# Five asynchronous master clocks, five generated clocks (divide ratios
# 2/3/4/5/8), and explicit asynchronous clock grouping so that CDC paths are
# not analysed as synchronous transfers.
#
# The periods below are deliberately tight: the baseline design does NOT meet
# them. That is the point -- the optimiser has to close the gap.
# ---------------------------------------------------------------------------

# ---- master clocks --------------------------------------------------------
create_clock -name clk_core -period 7.50 [get_ports clk_core]
create_clock -name clk_dsp  -period 6.00 [get_ports clk_dsp]
create_clock -name clk_mem  -period 1.45 [get_ports clk_mem]
create_clock -name clk_io   -period 4.20 [get_ports clk_io]
create_clock -name clk_sec  -period 5.00 [get_ports clk_sec]

# ---- generated clocks (dividers inside u_clkgen) --------------------------
create_generated_clock -name clk_core_div2 -source [get_ports clk_core] \
    -divide_by 2 [get_pins u_clkgen/clk_core_div2]
create_generated_clock -name clk_dsp_div3  -source [get_ports clk_dsp] \
    -divide_by 6 [get_pins u_clkgen/clk_dsp_div3]
create_generated_clock -name clk_mem_div4  -source [get_ports clk_mem] \
    -divide_by 4 [get_pins u_clkgen/clk_mem_div4]
create_generated_clock -name clk_io_div5   -source [get_ports clk_io] \
    -divide_by 10 [get_pins u_clkgen/clk_io_div5]
create_generated_clock -name clk_sec_div8  -source [get_ports clk_sec] \
    -divide_by 8 [get_pins u_clkgen/clk_sec_div8]

# ---- asynchronous relationships ------------------------------------------
# Each master clock and the clock it generates form one synchronous island.
# Everything between islands is a genuine CDC path and must not be timed.
set_clock_groups -asynchronous \
    -group {clk_core clk_core_div2} \
    -group {clk_dsp  clk_dsp_div3}  \
    -group {clk_mem  clk_mem_div4}  \
    -group {clk_io   clk_io_div5}   \
    -group {clk_sec  clk_sec_div8}

# ---- uncertainty / transition --------------------------------------------
set_clock_uncertainty -setup 0.05 [all_clocks]
set_clock_uncertainty -hold  0.03 [all_clocks]
set_clock_transition  0.05 [all_clocks]

# ---- I/O environment ------------------------------------------------------
set_driving_cell -lib_cell BUF_X4 -pin Z [all_inputs]
set_load 0.02 [all_outputs]

# Control ports are read in more than one domain, so they carry an input delay
# against every clock that samples them. The asynchronous clock groups above
# stop these from being analysed as cross-domain transfers.
set_input_delay  -clock clk_core 0.20 [get_ports {start status[*] alu_op[*] rs1_addr[*] rs2_addr[*] rd_addr[*] rd_we din[*]}]
set_input_delay  -clock clk_mem  0.20 -add_delay [get_ports {start abort status[*]}]
set_input_delay  -clock clk_io   0.20 -add_delay [get_ports {abort din[*]}]
set_input_delay  -clock clk_dsp  0.20 [get_ports {dsp_sel[*]}]
set_input_delay  -clock clk_sec  0.20 [get_ports {aes_key[*]}]

set_output_delay -clock clk_core 0.20 [get_ports {core_out[*]}]
set_output_delay -clock clk_dsp  0.20 [get_ports {dsp_out[*]}]
set_output_delay -clock clk_mem  0.20 [get_ports {mem_out[*]}]
set_output_delay -clock clk_io   0.20 [get_ports {io_out[*]}]
set_output_delay -clock clk_sec  0.20 [get_ports {sec_out[*]}]

# div_out mixes the divided domains and clk_mem.
set_output_delay -clock clk_mem      0.20 [get_ports {div_out[*]}]
set_output_delay -clock clk_dsp_div3 0.20 -add_delay [get_ports {div_out[*]}]
set_output_delay -clock clk_mem_div4 0.20 -add_delay [get_ports {div_out[*]}]
set_output_delay -clock clk_io_div5  0.20 -add_delay [get_ports {div_out[*]}]
set_output_delay -clock clk_sec_div8 0.20 -add_delay [get_ports {div_out[*]}]

# Reset ports are asynchronous.
set_false_path -from [get_ports {rst_core_n rst_dsp_n rst_mem_n rst_io_n rst_sec_n}]
