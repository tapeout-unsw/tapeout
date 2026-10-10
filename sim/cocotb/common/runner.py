"""Build one block with Verilator (or Icarus) and run its cocotb tests.

Each sim/cocotb/<block>/test_<block>.py ends with a pytest function that calls
run(); `make test-cocotb` runs pytest over sim/cocotb, so pytest finds them all.

Environment variables, all optional:
  SEED=<n>        seed for the tests' random stimulus (they print the one used)
  WAVES=1         write a waveform into the build directory (off in CI)
  SIM=icarus      run under Icarus Verilog instead of Verilator. Icarus models
                  X (unknown) values, so a flip-flop that is never reset shows
                  up as X on an output; Verilator only has 0 and 1.
  RANDOM_INIT=1   Verilator only: start every register at a random value
                  instead of 0, so a register that is never reset gives
                  different results from run to run (SEED picks the values).
"""

import os
from pathlib import Path

from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[3]


def run(block, sources, toplevel, test_module, parameters=None, tag=""):
    """Build `toplevel` from `sources` (paths relative to the repository root)
    into sim/obj_dir/cocotb_<block><tag>[_icarus]/ and run the cocotb tests in
    `test_module` against it."""
    sim = os.environ.get("SIM", "verilator")
    assert sim in ("verilator", "icarus"), f"SIM={sim}: use verilator or icarus"
    random_init = os.environ.get("RANDOM_INIT") == "1" and sim == "verilator"
    suffix = ("" if sim == "verilator" else f"_{sim}") + ("_randinit" if random_init else "")
    build_dir = ROOT / "sim" / "obj_dir" / f"cocotb_{block}{tag}{suffix}"

    build_args, plusargs = [], []
    timescale = None
    if sim == "icarus":
        build_args = ["-g2012"]
        timescale = ("1ns", "1ps")   # for files without a `timescale of their own
    elif random_init:
        build_args = ["--x-initial", "unique", "--x-assign", "unique"]
        plusargs = ["+verilator+rand+reset+2", f"+verilator+seed+{os.environ.get('SEED', '1')}"]

    runner = get_runner(sim)
    runner.build(
        sources=[ROOT / s for s in sources],
        hdl_toplevel=toplevel,
        parameters=parameters or {},
        build_args=build_args,
        build_dir=build_dir,
        timescale=timescale,
        always=True,
    )
    runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        plusargs=plusargs,
        build_dir=build_dir,
        test_dir=build_dir,
        timescale=timescale,
    )
