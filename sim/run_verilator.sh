#!/usr/bin/env bash
# Core: ./sim/run_verilator.sh [16|32] [test|all]
# SoC:  ./sim/run_soc.sh [test|sample|all] [16|32]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NREGS=${1:-16}
WHICH=${2:-all}
TOP=${SIM_TOP:-core_mc_tb}
case "$NREGS" in 16|32) ;; *) echo "NREGS must be 16 or 32" >&2; exit 2;; esac
case "$TOP" in core_mc_tb|soc_tb) ;; *) echo "Unknown simulation top: $TOP" >&2; exit 2;; esac
if (( $# > 2 )); then
    echo "Usage: $0 [16|32] [test|all]" >&2
    exit 2
fi
case "$WHICH" in
    all) TESTS=("$ROOT"/tests/*.hex) ;;
    sample) TESTS=(); for t in rv32e_test simple auipc jal lui addi lw sw beq; do
        TESTS+=("$ROOT/tests/$t.hex")
    done ;;
    *) TESTS=("$ROOT/tests/$WHICH.hex") ;;
esac
for h in "${TESTS[@]}"; do
    [[ -f "$h" ]] || { echo "Test image not found: $h" >&2; exit 2; }
done
command -v verilator >/dev/null || { echo "Verilator 5+ is required; run nix develop first." >&2; exit 2; }
command -v timeout >/dev/null || { echo "GNU timeout is required." >&2; exit 2; }

CORE_SRC=("$ROOT"/rtl/core/*.sv "$ROOT/rtl/mem/mem_access.sv")
case "$TOP" in
    core_mc_tb) SRC=("${CORE_SRC[@]}") ;;
    soc_tb)     SRC=("${CORE_SRC[@]}" "$ROOT/rtl/soc.sv" "$ROOT/rtl/bootloader.sv"
                     "$ROOT/rtl/mem/sram_macro.sv" "$ROOT/rtl/mem/sram_mem.sv" "$ROOT"/rtl/periph/uart_*.sv) ;;
esac

# Keep compiled simulators and requested waveforms; capture output temporarily
# to validate results and show diagnostics only when a command fails.
BUILD="$ROOT/sim/obj_dir/${TOP}_${NREGS}"
mkdir -p "$BUILD"
OUTPUT=$(mktemp -d)
trap 'rm -rf "$OUTPUT"' EXIT
if ! verilator --binary --timing --trace --top-module "$TOP" \
    "-GNREGS=$NREGS" --Mdir "$BUILD" -o simulator -j "${JOBS:-2}" \
    "${SRC[@]}" "$ROOT/tb/$TOP.sv" >"$OUTPUT/build.log" 2>&1; then
    cat "$OUTPUT/build.log" >&2
    exit 1
fi

pass=0; fail=0
for h in "${TESTS[@]}"; do
    name=$(basename "$h" .hex)
    args=("+HEX=$h")
    if [[ ${TRACE:-0} == 1 ]]; then args+=("+TRACE=$BUILD/$name.vcd"); fi
    status=0
    timeout "${SIM_TIMEOUT:-180}" "$BUILD/simulator" "${args[@]}" >"$OUTPUT/test.log" 2>&1 || status=$?
    if (( status == 0 )) && grep -q '^PASS ' "$OUTPUT/test.log" \
        && ! grep -qE '^(FAIL|TIMEOUT)' "$OUTPUT/test.log"; then
        pass=$((pass+1))
    else
        echo "FAIL $name (exit $status)" >&2
        cat "$OUTPUT/test.log" >&2
        fail=$((fail+1))
    fi
done
echo "$TOP NREGS=$NREGS : $pass passed, $fail failed"
(( fail == 0 ))
