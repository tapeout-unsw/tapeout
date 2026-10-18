RISCV_PREFIX ?= riscv32-none-elf-
RISCV_AS      := $(RISCV_PREFIX)as
RISCV_LD      := $(RISCV_PREFIX)ld
RISCV_OBJCOPY := $(RISCV_PREFIX)objcopy
PYTHON        ?= python3
VERILATOR     ?= verilator

BUILD_DIR := build
BIN_DIR   := bin
HEX_DIR   := hex
SIM_DIR   := ../sim/obj_dir/programs

PROGRAMS := $(patsubst %.S,%,$(notdir $(wildcard *.S)))
BINS     := $(addprefix $(BIN_DIR)/,$(addsuffix .bin,$(PROGRAMS)))
HEXES    := $(addprefix $(HEX_DIR)/,$(addsuffix .hex,$(PROGRAMS)))
RTL      := $(wildcard ../rtl/core/*.sv) ../rtl/periph/mmio.sv ../rtl/bus/arbiter.sv
SIM_ARGS ?=
NREGS    ?= 16
SIM      := $(SIM_DIR)/$(NREGS)/simulator
PROGRAM  ?= test

.PHONY: all bins run clean
.SECONDARY:
all: $(HEXES)
bins: $(BINS)

$(BUILD_DIR) $(BIN_DIR) $(HEX_DIR):
	mkdir -p $@

$(BUILD_DIR)/%.o: %.S | $(BUILD_DIR)
	$(RISCV_AS) -march=rv32e -mabi=ilp32e -o $@ $<

$(BUILD_DIR)/%.elf: $(BUILD_DIR)/%.o link.ld
	$(RISCV_LD) -m elf32lriscv --no-relax -T link.ld -o $@ $<

$(BIN_DIR)/%.bin: $(BUILD_DIR)/%.elf | $(BIN_DIR)
	$(RISCV_OBJCOPY) -O binary $< $@

$(HEX_DIR)/%.hex: $(BIN_DIR)/%.bin bin2hex.py | $(HEX_DIR)
	$(PYTHON) bin2hex.py $< $@

$(SIM): program_tb.sv $(RTL) ../tools/lint_waivers.vlt
	mkdir -p $(dir $@)
	cd .. && $(VERILATOR) --binary --timing -Wall --top-module program_tb \
		-GNREGS=$(NREGS) --Mdir sim/obj_dir/programs/$(NREGS) \
		-o simulator -j 2 tools/lint_waivers.vlt $(patsubst ../%,%,$(RTL)) programs/program_tb.sv

run: $(HEX_DIR)/$(PROGRAM).hex $(SIM)
	cd .. && ./sim/obj_dir/programs/$(NREGS)/simulator \
		+HEX=programs/$(HEX_DIR)/$(PROGRAM).hex $(SIM_ARGS)

clean:
	rm -rf $(BUILD_DIR) $(BIN_DIR) $(HEX_DIR) $(SIM_DIR)
