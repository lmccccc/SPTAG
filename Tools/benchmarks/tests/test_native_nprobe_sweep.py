from pathlib import Path
import subprocess
import tempfile
import unittest


class NativeNProbeSweep(unittest.TestCase):
    def test_native_array_parser(self):
        source = Path(__file__).with_name("native_nprobe_sweep.cpp")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "native_nprobe_sweep"
            subprocess.run(
                ["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 str(source), "-o", str(binary)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
