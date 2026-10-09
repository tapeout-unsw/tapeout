"""Build one block with Verilator and run its cocotb tests.

Each sim/cocotb/<block>/test_<block>.py ends with a pytest function that calls
run(); `make test-cocotb` runs pytest over sim/cocotb, so pytest finds them all.

Environment variables, all optional:
  SEED=<n>   seed for the tests' random stimulus (they print the one used)
  WAVES=1    write a waveform into the build directory (off in CI)
"""

from pathlib import Path

from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[3]


def run(block, sources, toplevel, test_module, parameters=None, tag=""):
    """Build `toplevel` from `sources` (paths relative to the repository root)
    into sim/obj_dir/cocotb_<block><tag>/ and run the cocotb tests in
    `test_module` against it."""
    build_dir = ROOT / "sim" / "obj_dir" / f"cocotb_{block}{tag}"
    runner = get_runner("verilator")
    runner.build(
        sources=[ROOT / s for s in sources],
        hdl_toplevel=toplevel,
        parameters=parameters or {},
        build_dir=build_dir,
        always=True,
    )
    runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        build_dir=build_dir,
        test_dir=build_dir,
    )
