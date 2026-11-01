#!/bin/bash
set -euo pipefail

# One entry point for the KV260 host application. Run this script as the
# normal ubuntu user; it uses sudo only when PYNQ needs to program/access PL.
if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as the normal ubuntu user: bash run.sh" >&2
    echo "Do not run the whole script with sudo." >&2
    exit 1
fi

APP_ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$APP_ROOT"

EXPECTED_BIT="18639aeb3f957221f62bdd93a8081413bbe9f001bac336fe4d384dbfa88858c1"
EXPECTED_HWH="05ab12e5549855ece4e28089c0c33d1d80b81962b27d774a040247aad0b0871a"

check_file() {
    local expected=$1
    local file=$2
    local actual
    actual=$(sha256sum "$file" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        echo "Checksum failed for $file; copy the host_app folder again." >&2
        echo "Expected: $expected" >&2
        echo "Actual:   $actual" >&2
        exit 1
    fi
}

echo "[1/5] Verifying the matched FPGA bitstream and hardware handoff..."
check_file "$EXPECTED_BIT" system_wrapper.bit
check_file "$EXPECTED_HWH" system_wrapper.hwh

# The package carries ARM64 wheels and Debian packages, so no network access
# is needed. Keep generated files owned by ubuntu rather than root.
if [ ! -x toolchain/usr/bin/riscv64-unknown-elf-gcc ] || \
   [ ! -x toolchain/usr/bin/riscv64-unknown-elf-objcopy ] || \
   [ ! -d vendor/flask ] || [ ! -d vendor/waitress ]; then
    echo "[2/5] Installing the bundled Python and RISC-V tools offline..."
    python3 kria_offline/install_offline.py
else
    echo "[2/5] Bundled Python and RISC-V tools are already installed."
fi

chmod +x kria_offline/run_host.sh

echo "[3/5] Proving that the bundled GCC can assemble RV32E..."
TOOLCHAIN_CHECK=$(mktemp /tmp/tapeout-rv32e-check.XXXXXX.o)
trap 'rm -f "$TOOLCHAIN_CHECK"' EXIT
"$APP_ROOT/toolchain/usr/bin/riscv64-unknown-elf-gcc" \
    -B"$APP_ROOT/toolchain/usr/lib/riscv64-unknown-elf/bin/" \
    -march=rv32e -mabi=ilp32e -ffreestanding -nostdlib \
    -x c -c /dev/null -o "$TOOLCHAIN_CHECK"
rm -f "$TOOLCHAIN_CHECK"
trap - EXIT

echo "[4/5] Loading the PYNQ overlay without AXI/MMIO accesses..."
sudo ./kria_offline/run_host.sh \
    --overlay ./system_wrapper.bit \
    --overlay-only

echo "[5/5] Starting the processor host interface..."
echo "Open http://<kria-ip>:8000 on a computer on the same network."
echo "Press Ctrl-C here to stop the server."
exec sudo ./kria_offline/run_host.sh --overlay ./system_wrapper.bit
