#!/usr/bin/env bash
# Elaborate soc_top with iverilog, Verilator and Yosys at both memory plans,
# with and without the data caches.
# Fails on any missing module, missing or extra port, or width mismatch.
#   ./sim/check_top.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CORE="rtl/core/core.sv rtl/core/decoder.sv rtl/core/imm_gen.sv rtl/core/alu.sv rtl/core/register_file.sv rtl/core/mem_access.sv rtl/core/amoswap.sv"
SRC="rtl/soc_top.sv rtl/bootloader.sv rtl/bus/arbiter.sv rtl/bus/addr_decode.sv \
     rtl/cache/icache.sv rtl/cache/icache_FSM.sv rtl/cache/icache_storage.sv rtl/cache/icache_lru.sv \
     rtl/cache/dcache.sv rtl/cache/dcache_storage.sv rtl/cache/dcache_FSM.sv rtl/cache/dcache_snoop.sv \
     rtl/mem/sram_macro.sv rtl/mem/sram_mem.sv rtl/periph/mmio.sv \
     rtl/periph/uart_rx.sv rtl/periph/uart_tx.sv $CORE"

status=0
for CFG in "10 0" "11 0" "10 1" "11 1"; do
    read -r RAW DC <<<"$CFG"
    echo "== RAW=$RAW DCACHE=$DC"
    out=$(iverilog -g2012 -s soc_top -P soc_top.RAW=$RAW -P soc_top.DCACHE=$DC -o /dev/null $SRC 2>&1 | grep -v 'sorry:' || true)
    if [ -n "$out" ]; then echo "iverilog: FAIL"; echo "$out"; status=1; else echo "iverilog: ok"; fi

    out=$(verilator --lint-only -Wall -Wno-fatal --top-module soc_top -GRAW=$RAW -GDCACHE=$DC $SRC 2>&1 || true)
    bad=$(echo "$out" | grep -E '%(Error|Warning-(WIDTH|PINMISSING|PINNOTFOUND|IMPLICIT|UNDRIVEN|MULTIDRIVEN))' || true)
    if [ -n "$bad" ]; then echo "verilator: FAIL"; echo "$bad"; status=1; else echo "verilator: ok"; fi

    out=$(yosys -q -p "read_verilog -sv $SRC; chparam -set RAW $RAW -set DCACHE $DC soc_top; hierarchy -check -top soc_top" 2>&1 || true)
    if echo "$out" | grep -qiE 'error|warning'; then echo "yosys: FAIL"; echo "$out"; status=1; else echo "yosys: ok"; fi
done
exit $status
