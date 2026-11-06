"""Synthetic complete fixtures test review gates; these are NOT native build proof."""
import copy
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/mpv'))
import windows_runtime as w


def pe_bytes(machine, dependencies):
    data = bytearray(2048)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, machine, 1)
    struct.pack_into('<H', data, 148, 240)
    struct.pack_into('<H', data, 152, 0x20b)
    struct.pack_into('<II', data, 272, 4096, (len(dependencies) + 1) * 20)
    struct.pack_into('<IIII', data, 400, 1536, 4096, 1536, 512)
    for i, name in enumerate(dependencies):
        offset = 800 + i * 128
        struct.pack_into('<I', data, 512 + i * 20 + 12, 4096 + offset - 512)
        data[offset:offset + len(name) + 1] = name.encode() + b'\0'
    return bytes(data)


class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output, self.cache = self.root / 'mpv', self.root / 'cache'
        self.output.mkdir()
        self.cache.mkdir()
        self.policy = copy.deepcopy(w._load(w.POLICY))
        self.arch = 'amd64'
        self.prefix = self.root / 'prefix'
        self.native = self.root / 'clang64'
        self.prefix.mkdir()
        self.native.mkdir()
        self._fixture()
        self.policy_file = self.root / 'policy.json'
        w.p.atomic_json(self.policy_file, self.policy)
        self.policy_patch = patch.object(w, 'POLICY', self.policy_file)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)
        self._capture()

    def _fixture(self):
        policy = self.policy
        for relative in policy['licenseSHA256']:
            file = self.output / 'licenses/windows-toolchain' / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(('fixture license: ' + relative).encode())
            policy['licenseSHA256'][relative] = w.p.digest(file)
        machine = policy['architectures'][self.arch]['machine']
        (self.output / 'libmpv-2.dll').write_bytes(pe_bytes(machine, ['libiconv-2.dll', 'kernel32.dll']))
        runtime = self.output / 'libiconv-2.dll'
        runtime.write_bytes(pe_bytes(machine, ['kernel32.dll']))
        policy['architectures'][self.arch]['runtimeDLLs']['libiconv-2.dll'] = w.p.digest(runtime)
        w.p.atomic_json(self.output / 'runtime-dlls.json', [{'name': runtime.name, 'sha256': w.p.digest(runtime), 'source': str(self.native / runtime.name)}])
        package_prefix = policy['architectures'][self.arch]['packagePrefix']
        self.packages = {package_prefix + k: v for k, v in policy['packageVersions'].items()}
        w.p.atomic_json(self.output / 'build-config/toolchain.json', {'packages': ''.join(f'{k} {v}\n' for k, v in self.packages.items()), 'runtimeSourceStatus': 'pending-toolchain-runtime-review'})
        self.original_sources = {}
        for name, entry in policy['sources'].items():
            members = {}
            if name == 'libiconv':
                members['COPYING.LIB'] = (self.output / 'licenses/windows-toolchain/libiconv/COPYING.LIB').read_bytes()
            elif name == 'mingw-w64':
                members['COPYING'] = (self.output / 'licenses/windows-toolchain/crt/COPYING').read_bytes()
            else:
                for folder in policy['recipeDirectories']:
                    members[f'{folder}/PKGBUILD'] = b'# synthetic fixture recipe\n'
                for path in policy['libiconvRecipeFiles']:
                    data = ('fixture recipe file ' + path).encode()
                    members['mingw-w64-libiconv/' + path] = data
                    policy['libiconvRecipeFiles'][path] = w._sha(data)
            archive = self.cache / entry['filename']
            with tarfile.open(archive, 'w:gz') as container:
                root = tarfile.TarInfo(entry['root'])
                root.type = tarfile.DIRTYPE
                container.addfile(root)
                for path, data in members.items():
                    item = tarfile.TarInfo(entry['root'] + '/' + path)
                    item.size = len(data)
                    container.addfile(item, io.BytesIO(data))
                # Legitimate source symlinks must remain in the original archive.
                link = tarfile.TarInfo(entry['root'] + '/source-link')
                link.type, link.linkname = tarfile.SYMTYPE, 'COPYING'
                container.addfile(link)
            entry['sha256'], entry['size'] = w.p.digest(archive), archive.stat().st_size
            self.original_sources[name] = archive.read_bytes()

    def _capture(self):
        project = self.root / 'work/mpv-build/entry.o'
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_bytes(b'project object')
        archive = self.prefix / 'libproject.a'
        archive.write_bytes(b'project archive')
        crt = self.native / 'crt2.o'
        crt.write_bytes(b'CRT startup')
        system_import = self.native / 'libkernel32.a'
        system_import.write_bytes(b'system import archive')
        self.map = self.root / 'final.map'
        self.map.write_text('Address Size Align Out In Symbol\n'
                            '140001000 20 16 .text\n'
                            '140001000 10 16 entry.o:(.text)\n'
                            f'140001010 10 16 {archive}(function.o):(.text)\n'
                            f'140001020 10 16 {crt}:(.text)\n'
                            f'140001030 10 16 {system_import}(import.o):(.text)\n')
        owner = self.policy['architectures'][self.arch]['packagePrefix'] + 'crt'
        with patch.object(w, '_owner', return_value=(owner, self.packages[owner])):
            w.capture_link_evidence(self.map, self.prefix, self.native, self.output)

    def complete(self):
        return w.complete_materials(self.output, 'windows/' + self.arch, self.cache, offline=True)

    def assert_blocked(self):
        with self.assertRaises((ValueError, FileNotFoundError, KeyError)):
            self.complete()
        self.assertFalse((self.output / 'windows-runtime-review.json').exists())
        self.assertEqual(w._load(self.output / 'build-config/toolchain.json')['runtimeSourceStatus'], 'pending-toolchain-runtime-review')

    def test_complete_preserves_original_archive_bytes_and_updates_status(self):
        report = self.complete()
        self.assertEqual(report['correspondingSourceStatus'], self.policy['correspondingSourceStatus'])
        self.assertEqual(report['linkInputCount'], 4)
        for name, entry in self.policy['sources'].items():
            self.assertEqual((self.output / 'sources/windows' / entry['filename']).read_bytes(), self.original_sources[name])
        self.assertTrue((self.output / 'rebuild/scripts/mpv/windows_runtime.py').is_file())
        self.assertEqual(w._load(self.output / 'build-config/toolchain.json')['runtimeSourceStatus'], report['correspondingSourceStatus'])

    def test_arm64_complete_fixture(self):
        self.arch = 'arm64'
        self._fixture()
        w.p.atomic_json(self.policy_file, self.policy)
        self._capture()
        self.assertEqual(self.complete()['target'], 'windows/arm64')

    def test_missing_source(self):
        (self.cache / self.policy['sources']['libiconv']['filename']).unlink()
        self.assert_blocked()

    def test_wrong_source(self):
        (self.cache / self.policy['sources']['mingw-w64']['filename']).write_bytes(b'wrong source')
        self.assert_blocked()

    def test_unknown_dll(self):
        (self.output / 'unknown.dll').write_bytes(pe_bytes(34404, []))
        self.assert_blocked()

    def test_wrong_architecture(self):
        (self.output / 'libmpv-2.dll').write_bytes(pe_bytes(43620, ['libiconv-2.dll']))
        self.assert_blocked()

    def test_wrong_runtime_bytes(self):
        with (self.output / 'libiconv-2.dll').open('ab') as file:
            file.write(b'changed')
        self.assert_blocked()

    def test_unknown_import(self):
        (self.output / 'libmpv-2.dll').write_bytes(pe_bytes(34404, ['unknown.dll', 'libiconv-2.dll']))
        self.assert_blocked()

    def test_wrong_package_version(self):
        file = self.output / 'build-config/toolchain.json'
        value = w._load(file)
        value['packages'] = value['packages'].replace('1.19.1-1', '1.19-1')
        w.p.atomic_json(file, value)
        self.assert_blocked()

    def test_wrong_license(self):
        (self.output / 'licenses/windows-toolchain/libc++/LICENSE').write_bytes(b'wrong')
        self.assert_blocked()

    def test_unknown_link_owner(self):
        file = self.output / 'build-config/windows-link-inputs.json'
        value = w._load(file)
        next(r for r in value['inputs'] if r['owner'] != 'project-fixed-source')['owner'] = 'unknown-package'
        w.p.atomic_json(file, value)
        self.assert_blocked()

    def test_missing_map(self):
        (self.output / 'build-config/mpv-link.map').unlink()
        self.assert_blocked()

    def test_changed_map(self):
        (self.output / 'build-config/mpv-link.map').write_text('missing original inputs')
        self.assert_blocked()

    def test_missing_link_input(self):
        file = self.output / 'build-config/windows-link-inputs.json'
        value = w._load(file)
        value['inputs'].pop()
        w.p.atomic_json(file, value)
        self.assert_blocked()

    def test_changed_original_link_archive(self):
        (self.prefix / 'libproject.a').write_bytes(b'changed')
        self.assert_blocked()

    def test_unknown_capture_owner(self):
        with patch.object(w, '_owner', return_value=('unknown-package', '1')):
            with self.assertRaisesRegex(ValueError, 'Unknown link owner'):
                w.capture_link_evidence(self.map, self.prefix, self.native, self.output)

    def test_capture_rejects_project_path_escape(self):
        outside = self.root / 'outside.o'
        outside.write_bytes(b'not a project object')
        self.map.write_text(f'140001000 10 16 {outside}:(.text)\n')
        with self.assertRaisesRegex(ValueError, 'outside fixed'):
            w.capture_link_evidence(self.map, self.prefix, self.native, self.output)

    def test_map_formats_ignore_symbols_and_keep_startup_objects(self):
        text = (' 0001:00000000 __entry 0000000140001000 f C:/clang64/lib/crt2.o\n'
                ' 0001:00000010 function 0000000140001010 f C:/clang64/lib/libc++.a:member.o\n'
                ' 0000:00000000 __ImageBase 0000000140000000 <absolute>\n')
        self.assertEqual(w._map_inputs(text), ['C:/clang64/lib/crt2.o', 'C:/clang64/lib/libc++.a'])
        with self.assertRaises(ValueError):
            w._map_inputs('no actual input table')


if __name__ == '__main__':
    unittest.main()
