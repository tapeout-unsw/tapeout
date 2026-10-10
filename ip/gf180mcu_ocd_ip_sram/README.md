# gf180mcu_ocd_ip_sram (vendored, Verilog views only)

Source: https://github.com/RTimothyEdwards/gf180mcu_ocd_ip_sram
Commit: efdbf734d806c2ac3b858d281a5b832625a8e928
License: Apache 2.0 (LICENSE in this folder). Files are unmodified copies.

3.3 V synchronous single-port SRAM macros (Open Circuit Design), 256x8, 512x8
and 1024x8.

| File | Used for |
|---|---|
| `*_sramNNNx8m8wm1.v` | Simulation with `+define+USE_SRAM_MACRO` |
| `*_sramNNNx8m8wm1__blackbox.v` | Synthesis (Yosys sees an opaque cell) |

Never read a model and its blackbox in the same run: both define the same
module. The default (behavioural) build reads neither.

To update: change the commit above and re-download with the same commands.
Layout views (GDS, LEF, Liberty) are handled by the PD flow, not stored here.
