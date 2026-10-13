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
RTL      := $(wildcard ../rtl/core/*.sv) ../rtl/periph/mmio.sv
TEST     ?= all

.PHONY: all bins test test-one clean
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

$(SIM_DIR)/%/simulator: %_tb.sv $(RTL)
	mkdir -p $(SIM_DIR)/$*
	cd .. && $(VERILATOR) --binary --timing --trace -Wno-fatal \
		--top-module $*_tb --Mdir sim/obj_dir/programs/$* \
		-o simulator -j 2 rtl/core/*.sv rtl/periph/mmio.sv \
		programs/$*_tb.sv

test:
	@test -n "$(PROGRAMS)" || { echo "No .S programs found" >&2; exit 1; }
	@if [ "$(TEST)" = all ]; then \
		for p in $(PROGRAMS); do \
			$(MAKE) --no-print-directory test-one TEST=$$p || exit 1; \
		done; \
	else \
		$(MAKE) --no-print-directory test-one TEST="$(TEST)"; \
	fi

test-one: $(HEX_DIR)/$(TEST).hex $(SIM_DIR)/$(TEST)/simulator
	cd .. && ./sim/obj_dir/programs/$(TEST)/simulator \
		+HEX=programs/$(HEX_DIR)/$(TEST).hex

clean:
	rm -rf $(BUILD_DIR) $(BIN_DIR) $(HEX_DIR) $(SIM_DIR)
