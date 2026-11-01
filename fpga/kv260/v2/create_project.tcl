# Create the complete KV260 Vivado project and block design.
#
# Usage from the repository root:
#   vivado -mode batch -source fpga/kv260/v2/create_project.tcl
#   vivado -mode batch -source fpga/kv260/v2/create_project.tcl -tclargs --build
#
# A non-option Tcl argument overrides the generated project directory.

set script_dir [file normalize [file dirname [info script]]]
set repo_root  $script_dir
set project_dir [file join $script_dir build]
set build_bitstream 0

foreach arg $argv {
    if {$arg eq "--build"} {
        set build_bitstream 1
    } elseif {[string match "-*" $arg]} {
        error "Unknown argument: $arg"
    } else {
        set project_dir [file normalize $arg]
    }
}

set project_name kv260_riscv
set design_name  system
set pl_frequency 50000000
set uart_baud    115200

puts "Repository:  $repo_root"
puts "Project:     $project_dir"
puts "Vivado:      [version -short]"

# Select the newest installed KV260 board definition instead of baking a board
# file revision into the repository.
set kv260_parts [lsort -dictionary [get_board_parts -quiet *kv260*]]
if {[llength $kv260_parts] == 0} {
    error "No KV260 board definition is installed. Install the KV260 board files through Vivado, then rerun this script."
}
set board_part [lindex $kv260_parts end]
set device_part [get_property PART_NAME [get_board_parts $board_part]]
puts "Board part:  $board_part"
puts "Device part: $device_part"

create_project $project_name $project_dir -part $device_part -force
set_property board_part $board_part [current_project]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

# Only synthesizable generic RTL is included. In particular, the GF180 macro
# models and black boxes are not project sources and USE_SRAM_MACRO is not set.
set rtl_sources [list \
    [file join $repo_root rtl core alu.sv] \
    [file join $repo_root rtl core decoder.sv] \
    [file join $repo_root rtl core imm_gen.sv] \
    [file join $repo_root rtl core mem_access.sv] \
    [file join $repo_root rtl core register_file.sv] \
    [file join $repo_root rtl core core.sv] \
    [file join $repo_root rtl cache icache_storage.sv] \
    [file join $repo_root rtl cache icache_lru.sv] \
    [file join $repo_root rtl cache icache_FSM.sv] \
    [file join $repo_root rtl cache icache.sv] \
    [file join $repo_root rtl cache dcache_storage.sv] \
    [file join $repo_root rtl cache dcache_FSM.sv] \
    [file join $repo_root rtl cache dcache_snoop.sv] \
    [file join $repo_root rtl cache dcache.sv] \
    [file join $repo_root rtl bus arbiter.sv] \
    [file join $repo_root rtl bus addr_decode.sv] \
    [file join $repo_root rtl mem sram_macro.sv] \
    [file join $repo_root rtl mem sram_mem.sv] \
    [file join $repo_root rtl periph uart_rx.sv] \
    [file join $repo_root rtl periph uart_tx.sv] \
    [file join $repo_root rtl periph mmio.sv] \
    [file join $repo_root rtl bootloader.sv] \
    [file join $repo_root rtl soc_top.sv] \
    [file join $script_dir kv260_pl_reset.v] \
    [file join $script_dir kv260_soc_wrapper.v] \
]

foreach source $rtl_sources {
    if {![file exists $source]} {
        error "Missing RTL source: $source"
    }
}

add_files -norecurse -fileset sources_1 $rtl_sources
set_property file_type SystemVerilog [get_files -of_objects [get_filesets sources_1] *.sv]
set_property verilog_define {} [get_filesets sources_1]
update_compile_order -fileset sources_1

create_bd_design $design_name
current_bd_design $design_name

# Processing system with the KV260 board preset, one AXI master into the PL,
# one 50 MHz PL clock and one fabric reset.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* ps]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
    -config {apply_board_preset "1"} $ps
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__NUM_FABRIC_RESETS {0} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {50} \
] $ps

# AXI peripherals controlled by Linux/Jupyter on the processing system.
set uart [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_uartlite:* uart]
set_property -dict [list \
    CONFIG.C_BAUDRATE $uart_baud \
    CONFIG.C_DATA_BITS {8} \
    CONFIG.C_USE_PARITY {0} \
    CONFIG.C_S_AXI_ACLK_FREQ_HZ $pl_frequency \
] $uart

set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:* control_status]
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {1} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {5} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] $gpio

set interconnect [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_smc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $interconnect

set pl_reset [create_bd_cell -type module -reference kv260_pl_reset pl_reset]

# Module reference containing the unmodified tapeout SoC.
set processor [create_bd_cell -type module -reference kv260_soc_wrapper processor]
set_property -dict [list \
    CONFIG.NREGS {16} \
    CONFIG.RAW {10} \
    CONFIG.CLK_FREQ $pl_frequency \
    CONFIG.BAUD_RATE $uart_baud \
] $processor

# Read-only status word presented through GPIO channel 2:
#   bit 0 done, bit 1 core_run, bit 2 lock, bits 4:3 exit[1:0].
set status_concat [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:* status_concat]
set_property -dict [list CONFIG.NUM_PORTS {4}] $status_concat

# Clock every PL block from the same 50 MHz PS fabric clock.
connect_bd_net [get_bd_pins $ps/pl_clk0] \
    [get_bd_pins $ps/maxihpm0_fpd_aclk] \
    [get_bd_pins $interconnect/aclk] \
    [get_bd_pins $uart/s_axi_aclk] \
    [get_bd_pins $gpio/s_axi_aclk] \
    [get_bd_pins $pl_reset/clk] \
    [get_bd_pins $processor/pl_clk]

# Reset the whole PL fabric for 32 clocks after each bitstream download. This
# deliberately does not depend on ps/pl_resetn0: Linux/PYNQ programs the PL but
# does not necessarily rerun the PS initialization that controls that output.
connect_bd_net [get_bd_pins $pl_reset/resetn] \
    [get_bd_pins $interconnect/aresetn] \
    [get_bd_pins $uart/s_axi_aresetn] \
    [get_bd_pins $gpio/s_axi_aresetn] \
    [get_bd_pins $processor/pl_resetn]

# PS AXI master to the two peripherals.
connect_bd_intf_net [get_bd_intf_pins $ps/M_AXI_HPM0_FPD] \
    [get_bd_intf_pins $interconnect/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins $interconnect/M00_AXI] \
    [get_bd_intf_pins $uart/S_AXI]
connect_bd_intf_net [get_bd_intf_pins $interconnect/M01_AXI] \
    [get_bd_intf_pins $gpio/S_AXI]

# Internal serial connection. This still exercises both UART RTL blocks and
# the hardware bootloader inside soc_top.
connect_bd_net [get_bd_pins $uart/tx] [get_bd_pins $processor/host_uart_tx]
connect_bd_net [get_bd_pins $processor/host_uart_rx] [get_bd_pins $uart/rx]

# GPIO channel 1 controls reset; channel 2 reads processor status.
connect_bd_net [get_bd_pins $gpio/gpio_io_o] [get_bd_pins $processor/software_reset]
connect_bd_net [get_bd_pins $processor/done] [get_bd_pins $status_concat/In0]
connect_bd_net [get_bd_pins $processor/dbg_core_run] [get_bd_pins $status_concat/In1]
connect_bd_net [get_bd_pins $processor/dbg_lock] [get_bd_pins $status_concat/In2]
connect_bd_net [get_bd_pins $processor/dbg_exit] [get_bd_pins $status_concat/In3]
connect_bd_net [get_bd_pins $status_concat/dout] [get_bd_pins $gpio/gpio2_io_i]

# Fixed addresses make the later notebook independent of Vivado's automatic
# address choices.
assign_bd_address -offset 0xA0000000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces $ps/Data] \
    [get_bd_addr_segs $uart/S_AXI/Reg]
assign_bd_address -offset 0xA0010000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces $ps/Data] \
    [get_bd_addr_segs $gpio/S_AXI/Reg]

validate_bd_design
save_bd_design
generate_target all [get_files ${design_name}.bd]

set bd_wrapper [make_wrapper -files [get_files ${design_name}.bd] -top]
add_files -norecurse $bd_wrapper
set_property top ${design_name}_wrapper [get_filesets sources_1]
update_compile_order -fileset sources_1

puts "Created $project_dir/$project_name.xpr"
puts "UARTLite: 0xA0000000"
puts "GPIO:     0xA0010000"

if {$build_bitstream} {
    file mkdir [file join $project_dir reports]
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1

    open_run impl_1
    report_utilization -file [file join $project_dir reports utilization.rpt]
    report_timing_summary -file [file join $project_dir reports timing_summary.rpt]

    set bit_file [get_property BITSTREAM.FILE [get_runs impl_1]]
    puts "Bitstream: $bit_file"
    puts "Reports:   [file join $project_dir reports]"
}
