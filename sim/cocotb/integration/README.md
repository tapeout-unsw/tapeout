# Level 2 tests (parked)

Neighbour-integration tests for the SRAM, kept for the next stage. **They
have not been run yet** and are not part of any make target or CI step.

| Module | Top level | Covers |
|---|---|---|
| `test_sram_core.py` | `sram_core_harness.sv`: the core's `mem_access` + `sram_mem` | One core's loads and stores, byte to word, with sign and zero extension (C1–C5) |
| `test_sram_bus.py` | `sram_bus_harness.sv`: real `arbiter` + SRAM side of `addr_decode` + `sram_mem` | Core 0's data port and its I-cache sharing the pipelined bus (B1–B6) |

Before using them at level 2:

1. Run them and fix what fails.
2. In `sram_bus_harness.sv`, replace the inline SRAM side of `addr_decode`
   with the real `addr_decode` once it is implemented.
3. Move the helpers they share with `sram_mem/sram_model.py` (`ref_model.py`
   here is a copy) into a common module.
4. Before running them with `SRAM_MACRO=1` in Verilator, add the dummy
   start-up access described in `sram_mem/README.md`; without it the OCD
   model ignores every access.

Run the same way as the unit tests:

```sh
python -m pytest -v sim/cocotb/integration
```
