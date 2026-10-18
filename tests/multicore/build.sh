#!/usr/bin/env bash
# Rebuild the .hex images. Needs clang and ld.lld.
set -euo pipefail
cd "$(dirname "$0")"
for s in ${1:-mc_*}.S; do
    n=${s%.S}
    clang --target=riscv32 -march=rv32e -mabi=ilp32e -mno-relax -c "$n.S" -o /tmp/$n.o
    ld.lld -m elf32lriscv --no-relax -T link.ld -o /tmp/$n.elf /tmp/$n.o
    llvm-objcopy -O binary /tmp/$n.elf /tmp/$n.bin
    od -An -v -tx4 -w4 /tmp/$n.bin | tr -d ' ' > "$n.hex"
done
