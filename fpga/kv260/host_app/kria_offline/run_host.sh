#!/bin/bash
set -eu

APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Kria-PYNQ installs its XRT variables, virtual environment and device-tree
# setup in this profile script. Load it for non-interactive/root launches too.
if [ -r /etc/profile.d/pynq_venv.sh ]; then
    . /etc/profile.d/pynq_venv.sh
fi
export XILINX_XRT="${XILINX_XRT:-/usr}"

export PYTHONPATH="$APP_ROOT/vendor:$APP_ROOT/src"
export PATH="$APP_ROOT/toolchain/usr/bin:$PATH"
export RISCV_PREFIX="riscv64-unknown-elf-"
# The server runs with sudo for MMIO access. Keep it from creating root-owned
# __pycache__ files inside the user-owned, replaceable host_app directory.
export PYTHONDONTWRITEBYTECODE=1

if [ -x /usr/local/share/pynq-venv/bin/python3 ]; then
    PYNQ_PYTHON=/usr/local/share/pynq-venv/bin/python3
else
    PYNQ_PYTHON=$(command -v python3)
fi

if ! "$PYNQ_PYTHON" -c 'import pynq' 2>/dev/null; then
    echo "PYNQ is not importable by $PYNQ_PYTHON." >&2
    echo "Run this app with the board's PYNQ Python environment." >&2
    exit 1
fi

exec "$PYNQ_PYTHON" -m tapeout_host.app "$@"
