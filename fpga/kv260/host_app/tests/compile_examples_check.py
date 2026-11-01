"""Compile the bundled examples using an available RISC-V toolchain."""
import tempfile
from pathlib import Path

from tapeout_host.compiler import compile_source


with tempfile.TemporaryDirectory() as directory:
    for source in sorted(Path("examples").glob("*.c")):
        build = compile_source(source, Path(directory) / source.stem)
        print(f"{source.name}: {len(build.image.data)} bytes")
