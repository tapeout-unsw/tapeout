"""Build cache examples into a temporary directory for RTL simulation."""
from pathlib import Path
from tapeout_host.compiler import compile_source

destination = Path("/tmp/tapeout-v2-sim/images")
destination.mkdir(parents=True, exist_ok=True)
for name in ("cache_memory", "cache_byte_lanes", "cache_handoff", "cache_benchmark", "dual_core_lock"):
    build = compile_source(Path("examples") / f"{name}.c", destination / name)
    data = build.image.data
    (destination / f"{name}.hex").write_text(
        "".join(f"{int.from_bytes(data[i:i+4], 'little'):08x}\n" for i in range(0, len(data), 4)),
        encoding="ascii",
    )
    print(f"{name}: {len(data)} bytes")
