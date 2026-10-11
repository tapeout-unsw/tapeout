# sram_mem level 1 (unit) tests

The unit is `sram_mem` together with its four `sram_macro` lanes, driven
directly through `sram_mem`'s ports and checked against a reference memory.
The test list (U1–U12) is in the docstring of `test_sram_unit.py`.

Every test runs six times:

| | 256 words (1 KiB) | 512 words (2 KiB) | 1024 words (4 KiB, `soc.sv`'s default) | Simulator |
|---|---|---|---|---|
| **behavioural** lanes (`sram_macro`'s own model) | ✓ | ✓ | ✓ | Verilator |
| **macro** lanes (OCD models in `ip/`, `USE_SRAM_MACRO`) | ✓ | ✓ | ✓ | Icarus |

The macro runs also prove the wrapper's active-low polarities: a wrong `CEN`
or `GWEN` fails every test.

### Two things the OCD models need

**Icarus, not Verilator, under cocotb.** cocotb always passes Verilator
`--public-flat-rw`, and with it Verilator generates C++ that uses the models'
timing constants (`specparam`s such as `Tdly`) without declaring them, so the
build fails. The macro runs therefore use Icarus (`SIM` can still force one
simulator for both).

**A `CEN` rise before the first access, in Verilator.** The model only becomes
operational after it sees `CEN` *rise* after time zero. A 4-state simulator
(Icarus, or real power-up) gives it that X → 1 rise; Verilator starts `CEN`
at 1, so the model silently ignores every access. `SramPort.start()` makes one
dummy read so `CEN` falls and rises before the tests begin. Any other
Verilator simulation that uses the macro models needs the same dummy access.

### When rdata is read

Inputs change on the falling edge, the SRAM samples them on the rising edge,
and `rdata` is read at the next falling edge. The macro's `Q` settles a short
delay after the clock edge, as in silicon, and the bus only needs the data
before the following rising edge. Reading at the rising edge itself would see
the previous value.

## Setup (once)

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r sim/requirements.txt
```

cocotb 2.x needs Verilator 5.036 or newer (`make env` checks).

## Running

```sh
python -m pytest -v sim/cocotb/sram_mem                        # all six runs
python -m pytest -v sim/cocotb/sram_mem -k "512 and macro"     # one run
SEED=12345 python -m pytest -v sim/cocotb/sram_mem             # repeat a random run
WAVES=1 python -m pytest -v sim/cocotb/sram_mem -k "256-behavioural"
SIM=icarus python -m pytest -v sim/cocotb/sram_mem             # Icarus instead of Verilator
```

Builds, logs, `results.xml` and waveforms go to `sim/obj_dir/cocotb_sram_mem_*`.
Random tests print their seed in the cocotb log; pass it back with `SEED` to
reproduce a failure exactly.
