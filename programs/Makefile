RISCV_PREFIX ?= riscv32-none-elf-
RISCV_AS      := $(RISCV_PREFIX)as
RISCV_LD      := $(RISCV_PREFIX)ld
RISCV_OBJCOPY := $(RISCV_PREFIX)objcopy
PYTHON        ?= python3
VERILATOR     ?= verilator

BUILD_DIR := build
BIN_DIR   := bin
HEX_DIR   := hex
SIM_DIR   := obj_dir

AVAILABLE_PROGRAMS := $(patsubst %.S,%,$(notdir $(wildcard *.S)))
BINS     := $(addprefix $(BIN_DIR)/,$(addsuffix .bin,$(AVAILABLE_PROGRAMS)))
HEXES    := $(addprefix $(HEX_DIR)/,$(addsuffix .hex,$(AVAILABLE_PROGRAMS)))
SOC_RTL  := $(sort $(wildcard ../rtl/core/*.sv ../rtl/bus/*.sv ../rtl/cache/*.sv ../rtl/periph/*.sv)) \
            ../rtl/soc_top.sv ../rtl/bootloader.sv ../rtl/mem/sram_macro.sv ../rtl/mem/sram_mem.sv
SIM_ARGS ?=
NREGS    ?= 16
SOC_SIM  := $(SIM_DIR)/soc_top_$(NREGS)/simulator
PROGRAM  ?=
PROGRAMS ?= $(PROGRAM)
SELECTED_PROGRAMS := $(patsubst %.S,%,$(strip $(PROGRAMS)))
UART     ?=
REGS     ?=

ifneq ($(filter $(NREGS),16 32),$(NREGS))
$(error NREGS must be 16 or 32)
endif

.PHONY: all bins run debug check-programs check-debug clean
.SECONDARY:
all: $(HEXES)
bins: $(BINS)

$(BUILD_DIR) $(BIN_DIR) $(HEX_DIR):
	mkdir -p $@

$(BUILD_DIR)/%.o: %.S $(wildcard *.inc) | $(BUILD_DIR)
	$(RISCV_AS) -march=rv32e -mabi=ilp32e -o $@ $<

$(BUILD_DIR)/%.elf: $(BUILD_DIR)/%.o link.ld
	$(RISCV_LD) -m elf32lriscv --no-relax -T link.ld -o $@ $<

$(BIN_DIR)/%.bin: $(BUILD_DIR)/%.elf | $(BIN_DIR)
	$(RISCV_OBJCOPY) -O binary $< $@

$(HEX_DIR)/%.hex: $(BIN_DIR)/%.bin bin2hex.py | $(HEX_DIR)
	$(PYTHON) bin2hex.py $< $@

$(SOC_SIM): ../tb/soc_top_tb.sv $(SOC_RTL)
	mkdir -p $(dir $@)
	cd .. && $(VERILATOR) --binary --timing --trace --top-module soc_top_tb \
		-GNREGS=$(NREGS) --Mdir programs/$(dir $@) \
		-o simulator -j 2 $(patsubst ../%,%,$(SOC_RTL)) tb/soc_top_tb.sv

check-programs:
	@test -n "$(SELECTED_PROGRAMS)" || { \
		echo 'Usage: make run PROGRAMS="first [second ...]" [UART=<expected output>]'; exit 2; }
	@for program in $(SELECTED_PROGRAMS); do \
		test -f "$$program.S" || { echo "Assembly source not found: $$program.S"; exit 2; }; \
	done

run: check-programs $(addprefix $(HEX_DIR)/,$(addsuffix .hex,$(SELECTED_PROGRAMS))) $(SOC_SIM)
	@set -- +DONE '+UART=$(UART)'; index=0; \
	for program in $(SELECTED_PROGRAMS); do \
		index=$$((index + 1)); \
		if [ "$$index" -eq 1 ]; then key=HEX; else key=HEX$$index; fi; \
		set -- "$$@" "+$$key=programs/$(HEX_DIR)/$$program.hex"; \
	done; \
	index=0; for expectation in $(REGS); do \
		index=$$((index + 1)); set -- "$$@" "+REG$$index=$$expectation"; \
	done; \
	cd .. && ./programs/$(SOC_SIM) "$$@" $(SIM_ARGS)

check-debug: check-programs
	@test "$(words $(SELECTED_PROGRAMS))" -eq 1 || { \
		echo 'Debug accepts one program; use run to load multiple programs.'; exit 2; }

debug: check-debug $(addprefix $(HEX_DIR)/,$(addsuffix .hex,$(SELECTED_PROGRAMS))) $(SOC_SIM)
	@set -- +HEX=programs/$(HEX_DIR)/$(SELECTED_PROGRAMS).hex +DEBUG; \
	index=0; for expectation in $(REGS); do \
		index=$$((index + 1)); set -- "$$@" "+REG$$index=$$expectation"; \
	done; \
	cd .. && ./programs/$(SOC_SIM) "$$@" $(SIM_ARGS)

clean:
	rm -rf $(BUILD_DIR) $(BIN_DIR) $(HEX_DIR) $(SIM_DIR)
