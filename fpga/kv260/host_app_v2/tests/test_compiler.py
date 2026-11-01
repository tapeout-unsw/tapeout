import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tapeout_host.compiler import compile_source


class CompilerTests(unittest.TestCase):
    def test_forces_gcc_to_use_binutils_beside_compiler(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if command[0] == "/bundle/bin/riscv64-unknown-elf-gcc":
                Path(command[command.index("-o") + 1]).touch()
            else:
                Path(command[-1]).write_bytes(b"\x13\x00\x00\x00")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            source = Path(directory) / "hello.c"
            source.write_text("int main(void) { return 0; }", encoding="utf-8")
            with (
                patch(
                    "tapeout_host.compiler._find_toolchain",
                    return_value=(
                        "/bundle/bin/riscv64-unknown-elf-gcc",
                        "/bundle/bin/riscv64-unknown-elf-objcopy",
                        Path("/bundle/lib/riscv64-unknown-elf/bin"),
                    ),
                ),
                patch("tapeout_host.compiler.subprocess.run", side_effect=fake_run),
            ):
                compile_source(source, Path(directory) / "build")

        self.assertEqual(commands[0][0], "/bundle/bin/riscv64-unknown-elf-gcc")
        self.assertEqual(commands[0][1], "-B/bundle/lib/riscv64-unknown-elf/bin/")
        self.assertEqual(commands[1][0], "/bundle/bin/riscv64-unknown-elf-objcopy")


if __name__ == "__main__":
    unittest.main()
