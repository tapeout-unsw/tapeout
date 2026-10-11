#!/usr/bin/env bash
# Run one block's unit testbench with iverilog.
#   ./sim/run_unit.sh arbiter | addr_decode | icache | sram_mem | mmio | all
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
declare -A RTL=(
    [arbiter]="rtl/bus/arbiter.sv"
    [addr_decode]="rtl/bus/addr_decode.sv"
    [icache]="rtl/cache/icache.sv"
    [sram_mem]="rtl/mem/sram_macro.sv rtl/mem/sram_mem.sv"
    [mmio]="rtl/periph/mmio.sv"
)
WHICH=${1:-all}
if [ "$WHICH" = all ]; then NAMES="arbiter addr_decode icache sram_mem mmio"; else NAMES="$WHICH"; fi
status=0
for n in $NAMES; do
    [ -n "${RTL[$n]:-}" ] || { echo "unknown block: $n" >&2; exit 2; }
    OUT=$(mktemp -d)/sim # so two-file entrys can be passed
    srcs=()
    for f in ${RTL[$n]}; do srcs+=("$ROOT/$f"); done
    if ! iverilog -g2012 -s ${n}_tb -o "$OUT" "${srcs[@]}" "$ROOT/tb/${n}_tb.sv"; then
        echo "FAIL    $n  does not compile"; status=1; continue
    fi
    r=$(timeout 60 vvp "$OUT" 2>&1 | grep -E "^(PASS|FAIL)|check failed" || true)
    echo "$r"
    echo "$r" | grep -q '^PASS' || status=1
done
exit $status
