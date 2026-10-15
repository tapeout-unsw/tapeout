#!/usr/bin/env bash
# check_env.sh - verify the dev environment for the multicore RV32 project.
#
#   ./tools/check_env.sh          toolchain A: what `make ci` needs (everyone)
#   ./tools/check_env.sh --full   also require the SW toolchain (RISC-V gcc, Spike)
#   ./tools/check_env.sh --pd     also check toolchain B (physical implementation, PD sub-team)
#   ./tools/check_env.sh --quiet  only print problems
#
# Exit code 0 = every required check passed. CI runs this too.
#
# The SW toolchain only warns by default because nothing in the repo needs it
# yet. Make it required (drop --full) once progs/ and the golden model land.

set -uo pipefail

# --- pinned versions ----------------------------------------------------------
VERILATOR_MIN="5.036"     # cocotb 2.x minimum; also covers --binary --timing
VERILATOR_PIN="5.052"
COCOTB_MIN="2.1.0"
PYTHON_MIN="3.11"

CHECK_PD=0
QUIET=0
FULL=0
for arg in "$@"; do
  case "$arg" in
    --pd) CHECK_PD=1 ;;
    --full) FULL=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else
  R=""; G=""; Y=""; B=""; N=""
fi

PASS=0; WARN=0; FAIL=0
TMPDIR_ENV="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_ENV"' EXIT

ok()   { PASS=$((PASS+1)); [ "$QUIET" -eq 1 ] || printf '  %sok%s    %-22s %s\n' "$G" "$N" "$1" "${2-}"; }
warn() { WARN=$((WARN+1)); printf '  %swarn%s  %-22s %s\n' "$Y" "$N" "$1" "${2-}"; }
bad()  { FAIL=$((FAIL+1)); printf '  %sFAIL%s  %-22s %s\n' "$R" "$N" "$1" "${2-}"; }
# needed: FAIL with --full, otherwise warn
needed() { if [ "$FULL" -eq 1 ]; then bad "$@"; else warn "$@"; fi; }
head2() { [ "$QUIET" -eq 1 ] || printf '\n%s%s%s\n' "$B" "$1" "$N"; }

# version_ge A B -> true if A >= B (dotted numeric compare)
version_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

have() { command -v "$1" >/dev/null 2>&1; }

# first_version <cmd> [args...] : print the first x.y[.z] found in the output
first_version() {
  "$@" 2>&1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1
}

platform() {
  case "$(uname -s)" in
    Darwin) echo "macOS" ;;
    Linux) grep -qi microsoft /proc/version 2>/dev/null && echo "WSL2" || echo "Linux" ;;
    *) uname -s ;;
  esac
}

[ "$QUIET" -eq 1 ] || printf '%sEnvironment check%s  (%s, %s)\n' "$B" "$N" "$(platform)" "$(date +%Y-%m-%d)"

# =============================================================================
head2 "Toolchain A: build, lint, simulate (everyone)"
# =============================================================================

for tool in git make; do
  if have "$tool"; then ok "$tool" "$(command -v "$tool")"; else bad "$tool" "not found"; fi
done

if have g++ || have clang++ || have c++; then
  ok "C++ compiler" "$(command -v g++ || command -v clang++ || command -v c++)"
else
  bad "C++ compiler" "install g++ (Linux) or Xcode command line tools (macOS)"
fi

# run_verilator.sh wraps every test in timeout(1)
if have timeout; then
  ok "timeout" "$(command -v timeout)"
else
  bad "timeout" "not found - macOS: brew install coreutils, then add its gnubin to PATH"
fi

# run_unit.sh uses associative arrays, which need bash 4 or newer (macOS ships 3.2)
bver=$(bash -c 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"' 2>/dev/null)
if [ -n "$bver" ] && version_ge "$bver" "4.0"; then
  ok "bash" "$bver ($(command -v bash))"
else
  bad "bash" "${bver:-not found} - need 4 or newer; macOS: brew install bash"
fi

if have python3; then
  pyver=$(first_version python3 --version)
  if version_ge "$pyver" "$PYTHON_MIN"; then ok "python3" "$pyver"
  else bad "python3" "$pyver found, need >= $PYTHON_MIN"; fi
else
  bad "python3" "not found"
fi

if have verilator; then
  vver=$(first_version verilator --version)
  if version_ge "$vver" "$VERILATOR_MIN"; then
    if [ "$vver" = "$VERILATOR_PIN" ]; then ok "verilator" "$vver"
    else ok "verilator" "$vver (team pin is $VERILATOR_PIN)"; fi
  else
    bad "verilator" "$vver found, need >= $VERILATOR_MIN - use the Nix shell or build from source"
  fi

  # a tool that exists and a tool that works are different things
  cat > "$TMPDIR_ENV/t.sv" <<'EOF'
module t (input logic clk, input logic d, output logic q);
  always_ff @(posedge clk) q <= d;
endmodule
EOF
  if verilator --lint-only -Wall "$TMPDIR_ENV/t.sv" >"$TMPDIR_ENV/lint.log" 2>&1; then
    ok "verilator lint" "lints a test module"
  else
    bad "verilator lint" "failed: $(head -n1 "$TMPDIR_ENV/lint.log")"
  fi
else
  bad "verilator" "not found"
fi

if have yosys; then
  ok "yosys" "$(first_version yosys -V) (make synth, make check-top)"
else
  bad "yosys" "not found - macOS: brew install yosys; Linux: use nix develop"
fi

if have iverilog; then
  ok "iverilog" "$(first_version iverilog -V) (make test-unit, make check-top)"
else
  bad "iverilog" "not found - macOS: brew install icarus-verilog; Linux: sudo apt install iverilog, or use nix develop"
fi

# make test-cocotb uses the repo's .venv if it exists, so check that Python.
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
if cver=$("$PY" -c 'import cocotb; print(cocotb.__version__)' 2>/dev/null); then
  if version_ge "$cver" "$COCOTB_MIN"; then ok "cocotb" "$cver ($PY, make test-cocotb)"
  else bad "cocotb" "$cver found, need >= $COCOTB_MIN (2.x changed the API)"; fi
else
  bad "cocotb" "not installed - run: python3 -m venv .venv && .venv/bin/pip install -r sim/requirements.txt"
fi

if have surfer; then
  ok "waveform viewer" "surfer"
elif have gtkwave; then
  ok "waveform viewer" "gtkwave"
else
  warn "waveform viewer" "neither surfer nor gtkwave (needed to debug, not to run tests)"
fi

# =============================================================================
head2 "SW toolchain: test programs and golden model$([ "$FULL" -eq 1 ] || echo " (not required yet)")"
# =============================================================================

RVGCC=""
for c in riscv64-unknown-elf-gcc riscv32-unknown-elf-gcc riscv64-elf-gcc; do
  have "$c" && { RVGCC="$c"; break; }
done
if [ -n "$RVGCC" ]; then
  ok "riscv gcc" "$RVGCC $(first_version "$RVGCC" --version)"
  printf '.section .text\n.global _start\n_start: addi x1, x0, 1\n' > "$TMPDIR_ENV/t.S"
  for isa in "rv32i ilp32" "rv32e ilp32e"; do
    set -- $isa
    if "$RVGCC" -march="$1" -mabi="$2" -nostdlib -nostartfiles \
          -o "$TMPDIR_ENV/t.elf" "$TMPDIR_ENV/t.S" >"$TMPDIR_ENV/rv.log" 2>&1; then
      ok "$1/$2 build" "links a test program"
    else
      needed "$1/$2 build" "failed - toolchain may lack multilib"
    fi
  done
  RVDUMP="${RVGCC%-gcc}-objdump"
  if have "$RVDUMP"; then ok "riscv objdump" "$RVDUMP"
  else needed "riscv objdump" "$RVDUMP not found - needed for the E-8 encoding check"; fi
else
  needed "riscv gcc" "not found (riscv64-unknown-elf-gcc)"
fi

if have spike; then ok "spike" "$(command -v spike)"
else needed "spike" "not found - needed for the golden model"; fi


# =============================================================================
if [ "$CHECK_PD" -eq 1 ]; then
head2 "Toolchain B: physical implementation (PD sub-team only)"

  if have nix || have nix-shell; then ok "nix" "$(command -v nix || command -v nix-shell)"
  elif have docker; then ok "docker" "$(command -v docker) (LibreLane --dockerized path)"
  else bad "nix or docker" "neither found"; fi

  if have librelane; then ok "librelane" "$(first_version librelane --version)"
  else warn "librelane" "not on PATH - expected unless you are inside its nix-shell"; fi

  for tool in openroad magic netgen klayout ciel; do
    if have "$tool"; then ok "$tool" "$(command -v "$tool")"
    else warn "$tool" "not on PATH (fine if it lives inside the LibreLane environment)"; fi
  done

  PDKR="${PDK_ROOT:-$HOME/.ciel}"
  if [ -d "$PDKR" ] && find "$PDKR" -maxdepth 4 -iname '*gf180mcuD*' -print -quit 2>/dev/null | grep -q .; then
    ok "gf180mcuD PDK" "$PDKR"
  else
    warn "gf180mcuD PDK" "not found under $PDKR - LibreLane fetches it on first run"
  fi
fi

# =============================================================================
printf '\n%ssummary%s  %s%d ok%s  %s%d warn%s  %s%d fail%s\n' \
  "$B" "$N" "$G" "$PASS" "$N" "$Y" "$WARN" "$N" "$R" "$FAIL" "$N"

if [ "$FAIL" -gt 0 ]; then
  printf 'Something required is missing. Fix the FAIL lines, then run this again.\n'
  exit 1
fi
printf 'Environment looks good.\n'
exit 0
