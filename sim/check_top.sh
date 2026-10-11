#!/usr/bin/env bash
# Elaborate soc_top with iverilog, Verilator and Yosys at both memory plans.
# Fails on any missing module, missing or extra port, or width mismatch.
#   ./sim/check_top.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CORE="rtl/core/core.sv rtl/core/decoder.sv rtl/core/imm_gen.sv rtl/core/alu.sv rtl/core/register_file.sv rtl/core/mem_access.sv"
SRC="rtl/soc_top.sv rtl/bootloader.sv rtl/bus/arbiter.sv rtl/bus/addr_decode.sv \
     rtl/cache/icache.sv rtl/mem/sram_macro.sv rtl/mem/sram_mem.sv rtl/periph/mmio.sv \
     rtl/periph/uart_rx.sv rtl/periph/uart_tx.sv $CORE"

status=0
for RAW in 10 11; do
    echo "== RAW=$RAW"
    out=$(iverilog -g2012 -s soc_top -P soc_top.RAW=$RAW -o /dev/null $SRC 2>&1 | grep -v 'sorry:' || true)
    if [ -n "$out" ]; then echo "iverilog: FAIL"; echo "$out"; status=1; else echo "iverilog: ok"; fi

    out=$(verilator --lint-only -Wall -Wno-fatal --top-module soc_top -GRAW=$RAW $SRC 2>&1 || true)
    bad=$(echo "$out" | grep -E '%(Error|Warning-(WIDTH|PINMISSING|PINNOTFOUND|IMPLICIT|UNDRIVEN|MULTIDRIVEN))' || true)
    if [ -n "$bad" ]; then echo "verilator: FAIL"; echo "$bad"; status=1; else echo "verilator: ok"; fi

    out=$(yosys -q -p "read_verilog -sv $SRC; chparam -set RAW $RAW soc_top; hierarchy -check -top soc_top" 2>&1 || true)
    if echo "$out" | grep -qiE 'error|warning'; then echo "yosys: FAIL"; echo "$out"; status=1; else echo "yosys: ok"; fi
done
exit $status
