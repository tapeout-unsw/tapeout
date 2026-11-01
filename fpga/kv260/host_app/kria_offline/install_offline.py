#!/usr/bin/env python3
"""Install the host application's Python and RISC-V tools without a network."""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


bundle = Path(__file__).resolve().parent
app = bundle.parent


def verify_downloads() -> None:
    manifest = bundle / "SHA256SUMS"
    for line in manifest.read_text(encoding="ascii").splitlines():
        expected, relative = line.split(maxsplit=1)
        target = bundle / relative
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Checksum failed for {relative}; copy the bundle again.")

if platform.machine().lower() not in {"aarch64", "arm64"}:
    raise SystemExit("This bundle is for the KV260's ARM64 Ubuntu system.")
if sys.version_info[:2] != (3, 10):
    raise SystemExit(
        f"This bundle targets Python 3.10; found {sys.version.split()[0]}. "
        "Tell us this version and we will generate matching wheels."
    )

verify_downloads()
print("Offline bundle checksums passed.")

vendor = app / "vendor"
toolchain = app / "toolchain"
for generated in (vendor, toolchain):
    if generated.exists():
        shutil.rmtree(generated)
    generated.mkdir()

wheels = sorted((bundle / "wheels").glob("*.whl"))
debs = sorted((bundle / "debs").glob("*.deb"))
if not wheels or len(debs) != 2:
    raise SystemExit("Offline bundle is incomplete; expected Python wheels and two .deb files.")

for wheel in wheels:
    print(f"Extracting {wheel.name}")
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(vendor)

for deb in debs:
    print(f"Extracting {deb.name}")
    subprocess.run(["dpkg-deb", "-x", str(deb), str(toolchain)], check=True)

required = [
    toolchain / "usr/bin/riscv64-unknown-elf-gcc",
    toolchain / "usr/bin/riscv64-unknown-elf-objcopy",
    vendor / "flask",
    vendor / "waitress",
]
missing = [str(path) for path in required if not path.exists()]
if missing:
    raise SystemExit("Installation incomplete; missing: " + ", ".join(missing))

print("\nOffline dependencies are ready.")
