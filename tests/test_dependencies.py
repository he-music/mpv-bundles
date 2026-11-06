from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/mpv"))
import pe
import prepare


class DependencyTests(unittest.TestCase):
    def test_pe_reads_import_table_and_ignores_unrelated_dll_strings(self):
        data = bytearray(0x600)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        coff = 0x84
        struct.pack_into("<HH", data, coff, 0x8664, 1)
        struct.pack_into("<H", data, coff + 16, 240)
        optional = coff + 20
        struct.pack_into("<H", data, optional, 0x20b)
        struct.pack_into("<I", data, optional + 108, 16)
        struct.pack_into("<II", data, optional + 112 + 8, 0x1000, 40)
        section = optional + 240
        struct.pack_into("<IIII", data, section + 8, 0x400, 0x1000, 0x400, 0x200)
        struct.pack_into("<IIIII", data, 0x200, 0, 0, 0, 0x1080, 0)
        data[0x280:0x28b] = b"libc++.dll\0"
        data[0x380:0x391] = b"not-imported.dll\0"
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "libmpv-2.dll"
            file.write_bytes(data)
            self.assertEqual(pe.imports(file), ["libc++.dll"])
            self.assertEqual(prepare.binary_arches(file), {"amd64"})
            self.assertFalse(pe.is_system_dll("libc++.dll"))
            self.assertTrue(pe.is_system_dll("api-ms-win-crt-math-l1-1-0.dll"))

    def test_linux_rejects_build_machine_runpath(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "libmpv.so.2").write_bytes(b"test")
            dynamic = "(NEEDED) Shared library: [libc.so.6]\n(RUNPATH) Library runpath: [/tmp/build/prefix/lib]"
            with patch.object(prepare, "run", return_value=dynamic):
                with self.assertRaisesRegex(ValueError, "runtime search path"):
                    prepare.validate_dependencies(root, "linux")
            with patch.object(prepare, "run", return_value=dynamic.replace("/tmp/build/prefix/lib", "$ORIGIN")):
                self.assertEqual(prepare.validate_dependencies(root, "linux"), {"libmpv.so.2": ["libc.so.6"]})

    def test_macos_rejects_missing_loader_path_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "libmpv.2.dylib").write_bytes(b"test")
            output = "file:\n\t@loader_path/missing.dylib (compatibility version 1.0.0)\n"
            with patch.object(prepare, "run", return_value=output):
                with self.assertRaisesRegex(ValueError, "undeclared dependencies"):
                    prepare.validate_dependencies(root, "darwin")
            (root / "missing.dylib").write_bytes(b"test")
            with patch.object(prepare, "run", return_value=output):
                self.assertEqual(len(prepare.validate_dependencies(root, "darwin")), 2)


if __name__ == "__main__":
    unittest.main()
