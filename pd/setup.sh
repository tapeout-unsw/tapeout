#!/usr/bin/env bash
# Makes a LibreLane workspace for Snowy: a wafer.space template checkout with
# the files in pd/ copied over and src/snowy linked to this repo's rtl.
# Rerunning it copies pd/ over again, so edit the files here, not in the workspace.
# usage: pd/setup.sh [workspace dir, default ~/ws-snowy]
set -euo pipefail

TEMPLATE=${TEMPLATE:-https://github.com/wafer-space/gf180mcu-project-template}
TEMPLATE_REV=6876569f06bd66bec83e9a3856d9524d1bb33eac
REPO=$(cd "$(dirname "$0")/.." && pwd)
WS=${1:-$HOME/ws-snowy}

if [ ! -d "$WS/.git" ]; then
    git clone -q "$TEMPLATE" "$WS"
    git -C "$WS" checkout -q "$TEMPLATE_REV"
fi

cp "$REPO/pd/src/chip_core.sv"               "$WS/src/"
cp "$REPO/pd/librelane/config.yaml"          "$WS/librelane/"
cp "$REPO/pd/librelane/macros/macros_3v3.yaml" "$WS/librelane/macros/"
cp "$REPO/pd/librelane/pdn/pdn_3v3_sram.tcl" "$WS/librelane/pdn/"
ln -sfn "$REPO/rtl" "$WS/src/snowy"

echo "workspace ready in $WS. Run these separately:"
echo "  cd $WS && nix-shell"
echo "  make librelane SLOT=0p5x0p5 SCL=gf180mcu_as_sc_mcu7t3v3 PAD=gf180mcu_ocd_io SRAM=gf180mcu_ocd_ip_sram"
echo "  python3 $REPO/pd/summary.py"
