#!/usr/bin/env python3
"""Fail fast if a KV260 BIT/HWH pair does not match the host application."""

from __future__ import annotations

import argparse
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path


def _parameters(module: ET.Element) -> dict[str, str]:
    return {
        parameter.attrib["NAME"]: parameter.attrib["VALUE"]
        for parameter in module.findall("./PARAMETERS/PARAMETER")
    }


def verify(bitstream: Path) -> None:
    bitstream = bitstream.resolve()
    handoff = bitstream.with_suffix(".hwh")
    if not bitstream.is_file() or bitstream.stat().st_size == 0:
        raise SystemExit(f"missing or empty bitstream: {bitstream}")
    if not handoff.is_file() or handoff.stat().st_size == 0:
        raise SystemExit(f"missing or empty matching HWH: {handoff}")

    modules = {
        module.attrib.get("INSTANCE"): module
        for module in ET.parse(handoff).getroot().iter("MODULE")
    }
    required = {"uart", "control_status", "processor", "pl_reset"}
    missing = sorted(required - modules.keys())
    if missing:
        raise SystemExit("HWH is missing required modules: " + ", ".join(missing))

    expected = {"uart": "0xA0000000", "control_status": "0xA0010000"}
    for name, address in expected.items():
        actual = _parameters(modules[name]).get("C_BASEADDR")
        if actual != address:
            raise SystemExit(f"{name} address is {actual}, expected {address}")

    clock = _parameters(modules["processor"]).get("CLK_FREQ")
    if clock != "50000000":
        raise SystemExit(f"processor clock is {clock}, expected 50000000")
    if "reset_not" in modules or "rst_pl" in modules:
        raise SystemExit("HWH still contains the unsafe PS-dependent reset path")

    digest = hashlib.sha256(bitstream.read_bytes()).hexdigest()
    print(f"PASS    BIT/HWH contract ({bitstream.stat().st_size} bytes)")
    print(f"SHA256  {digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bitstream", type=Path)
    verify(parser.parse_args().bitstream)
