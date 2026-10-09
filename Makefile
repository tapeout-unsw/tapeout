# Toolchain A entry point: lint, synthesis and elaboration checks, simulation.
#
# CI runs exactly these targets, so a green `make ci` locally means a green CI
# run, give or take tool versions. `make help` lists everything.

# Loops end each command with `|| exit 1` so a failure stops the target even
# on macOS's make 3.81, which ignores .SHELLFLAGS.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

RTL   := $(sort $(wildcard rtl/*.sv rtl/*/*.sv))
BUILD := build

NREGS ?= 16
TEST  ?= all
BLOCK ?= all
SYNTH_FLAGS ?= -noabc
SYNTH_TIMEOUT ?= 120s

# Unit testbenches with real checks. A block's testbench prints "no checks
# written yet" until its owner writes them; add the block here at that point
# and `make ci` (and CI) will require it to pass.
# Blocks: arbiter addr_decode icache sram_mem mmio
UNIT_GATED :=

# cocotb block tests, one folder per block under sim/cocotb/. They need the
# virtualenv: python -m pip install -r sim/requirements.txt. As with
# UNIT_GATED, add a block here once its RTL passes its tests; `make ci` and CI
# then require it to stay green.
# Blocks: icache
COCOTB_GATED :=

.PHONY: help env lint synth synth-full check-top test test-core test-soc test-all \
        test-unit test-unit-gated test-cocotb test-cocotb-gated waves ci clean

help:
	@echo "make env                  check the toolchain A install"
	@echo "make lint                 verilator -Wall on the single-core soc, 16 and 32 registers"
	@echo "make synth                yosys synthesis check without ABC, 16 and 32 registers"
	@echo "make synth-full           full generic synthesis including ABC gate optimization"
	@echo "make check-top            elaborate the dual-core soc_top (iverilog, verilator, yosys)"
	@echo "make test                 core and SoC program tests at NREGS=$(NREGS)"
	@echo "make test-core            core tests     (NREGS=16|32, TEST=all|<name>)"
	@echo "make test-soc             SoC boot tests (NREGS=16|32, TEST=all|sample|<name>)"
	@echo "make test-all             core and SoC tests at both register counts"
	@echo "make test-unit            block unit testbenches (BLOCK=all|arbiter|addr_decode|icache|sram_mem|mmio)"
	@echo "make test-unit-gated      only the unit testbenches listed in UNIT_GATED"
	@echo "make test-cocotb          cocotb block tests (BLOCK=all|icache; SEED=<n>, WAVES=1)"
	@echo "make test-cocotb-gated    only the cocotb blocks listed in COCOTB_GATED"
	@echo "make waves TEST=<name>    run one core test and dump a VCD"
	@echo "make ci                   everything CI requires"
	@echo "make clean                remove build outputs"

env:
	@./tools/check_env.sh --quiet

# Strict lint covers the single-core soc, which contains the core, memory,
# bootloader and UART. The dual-core soc_top still wires up stub blocks, so it
# is checked by check-top instead, which only fails on serious problems.
lint:
	@for n in 16 32; do \
	  echo "lint soc, NREGS=$$n"; \
	  verilator --quiet --lint-only -Wall --top-module soc -GNREGS=$$n tools/lint_waivers.vlt $(RTL) || exit 1; \
	done
	@echo "lint clean"

# Generic synthesis: no PDK needed. Catches latches, multiple drivers, logic
# loops and unsynthesisable constructs. MEM_WORDS is shrunk because the
# behavioural RAM would otherwise become thousands of flip-flops; on silicon
# that memory is an SRAM macro, not logic. PR checks skip ABC gate optimization;
# synth-full retains that flow. Bound each tool process and report failures.
synth:
	@for n in 16 32; do \
	  status=0; \
	  timeout --kill-after=10s $(SYNTH_TIMEOUT) yosys -Q -q -p " \
	    read_verilog -sv -DSYNTHESIS $(RTL); \
	    chparam -set NREGS $$n -set MEM_WORDS 16 soc; \
	    hierarchy -top soc -check; \
	    proc; opt; check -assert; \
	    synth -top soc $(SYNTH_FLAGS); check -assert" || status=$$?; \
	  if [ "$$status" -ne 0 ]; then \
	    echo "synthesis failed, NREGS=$$n, exit=$$status (124 indicates timeout; 137 indicates forced termination)" >&2; \
	    exit "$$status"; \
	  fi; \
	  echo "synthesis passed, NREGS=$$n"; \
	done

synth-full:
	@$(MAKE) --no-print-directory synth SYNTH_FLAGS=

# Bastian's elaboration check of soc_top at both RAM plans (RAW=10 and 11).
check-top:
	@./sim/check_top.sh

test-core:
	@./sim/run_verilator.sh $(NREGS) $(TEST)

test-soc:
	@./sim/run_soc.sh $(TEST) $(NREGS)

test: test-core test-soc

test-all:
	@for n in 16 32; do \
	  $(MAKE) --no-print-directory test-core NREGS=$$n TEST=all || exit 1; \
	  $(MAKE) --no-print-directory test-soc  NREGS=$$n TEST=all || exit 1; \
	done

test-unit:
	@./sim/run_unit.sh $(BLOCK)

test-unit-gated:
	@if [ -z "$(strip $(UNIT_GATED))" ]; then \
	  echo "no unit testbenches gated yet (see UNIT_GATED in the Makefile)"; \
	else \
	  for b in $(UNIT_GATED); do ./sim/run_unit.sh $$b || exit 1; done; \
	fi

test-cocotb:
	@python -m pytest -q sim/cocotb$(if $(filter-out all,$(BLOCK)),/$(BLOCK))

test-cocotb-gated:
	@if [ -z "$(strip $(COCOTB_GATED))" ]; then \
	  echo "no cocotb blocks gated yet (see COCOTB_GATED in the Makefile)"; \
	else \
	  python -m pytest -q $(addprefix sim/cocotb/,$(COCOTB_GATED)) || exit 1; \
	fi

waves:
	@if [ "$(TEST)" = all ]; then echo "usage: make waves TEST=<name> [NREGS=16|32]"; exit 2; fi
	@TRACE=1 ./sim/run_verilator.sh $(NREGS) $(TEST)
	@echo "open with: surfer sim/obj_dir/core_mc_tb_$(NREGS)/$(TEST).vcd"

ci: env lint synth check-top test-all test-unit-gated test-cocotb-gated

clean:
	rm -rf sim/obj_dir $(BUILD)
