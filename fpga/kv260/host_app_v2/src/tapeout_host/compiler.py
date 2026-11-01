"""Turn one uploaded C or assembly source file into a bare-metal RV32E image."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from .protocol import ProgramImage


class CompileError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


@dataclass(frozen=True)
class BuildResult:
    image: ProgramImage
    elf_path: Path
    binary_path: Path
    log: str
    compiler: str


def _find_toolchain() -> tuple[str, str, Path | None]:
    override = os.environ.get("RISCV_PREFIX")
    prefixes = [override] if override else []
    prefixes.extend(["riscv32-none-elf-", "riscv64-unknown-elf-"])
    for prefix in prefixes:
        if not prefix:
            continue
        gcc = shutil.which(prefix + "gcc")
        objcopy = shutil.which(prefix + "objcopy")
        if gcc and objcopy:
            # Debian stores unprefixed as/ld for this cross compiler under
            # usr/lib/<target>/bin. Point GCC there explicitly; -B usr/bin is
            # insufficient because that directory contains only prefixed
            # names such as riscv64-unknown-elf-as. Without this, relocated
            # GCC can fall back to Ubuntu's native ARM assembler.
            binary_directory = Path(gcc).resolve().parent
            target = prefix.removesuffix("-")
            companion_directory = binary_directory.parent / "lib" / target / "bin"
            if not (
                (companion_directory / "as").is_file()
                and (companion_directory / "ld").is_file()
            ):
                companion_directory = None
            return gcc, objcopy, companion_directory
    raise CompileError(
        "no bare-metal RISC-V compiler found; install gcc-riscv64-unknown-elf "
        "and binutils-riscv64-unknown-elf, or set RISCV_PREFIX"
    )


def compile_source(source: Path, output_dir: Path) -> BuildResult:
    if source.suffix.lower() not in {".c", ".s"}:
        raise CompileError("source input must end in .c, .s or .S")

    gcc, objcopy, tool_directory = _find_toolchain()
    output_dir.mkdir(parents=True, exist_ok=True)
    elf_path = output_dir / "program.elf"
    binary_path = output_dir / "program.bin"
    runtime_root = files("tapeout_host").joinpath("runtime")

    with as_file(runtime_root) as runtime:
        command = [gcc]
        if tool_directory is not None:
            command.append(f"-B{tool_directory}{os.sep}")
        command.extend([
            "-march=rv32e",
            "-mabi=ilp32e",
            "-Os",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-common",
            "-fno-pic",
            "-ffunction-sections",
            "-fdata-sections",
            "-mstrict-align",
            "-mno-relax",
            # The runtime deliberately does not initialize gp. Prevent GCC
            # from placing small globals in gp-relative .sdata/.sbss sections.
            "-msmall-data-limit=0",
            "-nostdlib",
            "-nostartfiles",
            f"-I{runtime}",
            f"-Wl,-T,{runtime / 'link.ld'}",
            "-Wl,--no-relax",
            "-Wl,--gc-sections",
            "-Wl,-Map," + str(output_dir / "program.map"),
            str(runtime / "start.S"),
            str(runtime / "runtime.c"),
            str(source),
            "-o",
            str(elf_path),
        ])
        compiled = subprocess.run(command, text=True, capture_output=True, check=False)
        log = "$ " + " ".join(command) + "\n" + compiled.stdout + compiled.stderr
        if compiled.returncode:
            raise CompileError("RISC-V compilation failed", log)

        converted = subprocess.run(
            [objcopy, "-O", "binary", str(elf_path), str(binary_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        log += "$ " + " ".join(converted.args) + "\n" + converted.stdout + converted.stderr
        if converted.returncode:
            raise CompileError("objcopy failed", log)

    try:
        image = ProgramImage.from_path(binary_path)
    except ValueError as exc:
        raise CompileError(str(exc), log) from exc
    return BuildResult(image, elf_path, binary_path, log, gcc)
