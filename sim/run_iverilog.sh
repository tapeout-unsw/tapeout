#!/usr/bin/env bash
# Run the test suite with Icarus Verilog.
#   ./sim/run_iverilog.sh            # all tests, RV32I (NREGS=32)
#   ./sim/run_iverilog.sh 16         # all tests, RV32E
#   ./sim/run_iverilog.sh 16 addi    # one test
set -u
NREGS=${1:-32}
ONLY=${2:-}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/rtl/core/core.sv \
     $ROOT/rtl/core/decoder.sv $ROOT/rtl/core/imm_gen.sv \
     $ROOT/rtl/core/mem_access.sv $ROOT/rtl/core/alu.sv $ROOT/rtl/core/register_file.sv \
     $ROOT/tb/core_mc_tb.sv"
OUT=$(mktemp -d)/sim
iverilog -g2012 -s core_mc_tb -o "$OUT" -Pcore_mc_tb.NREGS=$NREGS $SRC 2>&1 \
  | grep -viE "sorry:|constant selects" || true
pass=0; fail=0; failed=""
for h in $(ls "$ROOT"/tests/*.hex | sort); do
  b=$(basename "$h" .hex)
  [ -n "$ONLY" ] && [ "$b" != "$ONLY" ] && continue
  r=$(timeout 180 vvp "$OUT" +HEX="$h" ${VVP_ARGS:-} 2>/dev/null | grep -E "^(PASS|FAIL|TIMEOUT)")
  printf "%s\n" "$r"
  case "$r" in PASS*) pass=$((pass+1));; *) fail=$((fail+1)); failed="$failed $b";; esac
done
echo "------------------------------------------------"
echo "NREGS=$NREGS : $pass passed, $fail failed"
[ -n "$failed" ] && { echo "failing:$failed"; exit 1; }
exit 0
