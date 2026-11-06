"""Synthetic complete fixtures test review gates; these are NOT native build proof."""
import copy
import io
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/mpv'))
import windows_runtime as w

# Minimal rows copied from CI 42173608887's real probe map. The tar bytes below
# are synthetic; this fixture does not claim to reproduce a native build.
REAL_MAP_EXCERPT = (
    '00001000 0000039b    16         D:/a/_temp/msys64/clang64/lib/dllcrt2.o:(.text)\n'
    '000013a0 00000027    16         probe.obj:(.text)\n'
    '000013c8 00000000     4         charconv.cpp.obj:(.text)\n'
    '0000be44 00000000     4         Unwind-seh.cpp.obj:(.text)\n'
)


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
        # Canonical paths matter on macOS, where /var may resolve to /private/var.
        self.root = Path(self.temp.name).resolve()
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
        project = self.root / 'work/mpv-build/probe.obj'
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_bytes(b'project object')
        archive = self.prefix / 'libproject.a'
        archive.write_bytes(b'project archive')
        crt = self.native / 'dllcrt2.o'
        crt.write_bytes(b'CRT startup')
        system_import = self.native / 'libkernel32.a'
        system_import.write_bytes(b'system import archive')
        libcxx = self.native / 'libc++.a'
        libcxx.write_bytes(b'synthetic libc++ archive containing charconv.cpp.obj')
        unwind = self.native / 'libunwind.a'
        unwind.write_bytes(b'synthetic libunwind archive containing Unwind-seh.cpp.obj')
        self.input_files = [project, archive, crt, system_import, libcxx, unwind]
        self.map = self.root / 'final.map'
        self.map.write_text(REAL_MAP_EXCERPT)
        self.repro = self.map.with_suffix('.repro.tar')
        self.repro_members = [('final/response.txt', b'-dll synthetic response\n'),
                              ('final/version.txt', b'LLD 23.1.5 synthetic fixture\n')]
        self.repro_members += [('final/' + file.as_posix().replace(':', '').lstrip('/'), file.read_bytes()) for file in self.input_files]
        self._write_repro()
        self._recapture()

    def _write_repro(self):
        with tarfile.open(self.repro, 'w:') as container:
            for name, data in self.repro_members:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                container.addfile(member, io.BytesIO(data))

    def _fixed_owner(self, path):
        # Fixture ownership is assigned by full original path, never by map names.
        suffix = {self.native / 'libc++.a': 'libc++', self.native / 'libunwind.a': 'libunwind'}.get(path, 'crt')
        owner = self.policy['architectures'][self.arch]['packagePrefix'] + suffix
        return owner, self.packages[owner]

    def _recapture(self):
        with patch.object(w, '_owner', side_effect=self._fixed_owner):
            return w.capture_link_evidence(self.map, self.prefix, self.native, self.output)

    @property
    def manifest_file(self):
        return self.output / 'build-config/windows-link-reproducer.json'

    @property
    def evidence_file(self):
        return self.output / 'build-config/windows-link-inputs.json'

    def _change_manifest(self, mutate, rebind=False):
        value = w._load(self.manifest_file)
        mutate(value)
        w.p.atomic_json(self.manifest_file, value)
        if rebind:
            evidence = w._load(self.evidence_file)
            evidence['manifestSHA256'] = w.p.digest(self.manifest_file)
            w.p.atomic_json(self.evidence_file, evidence)

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
        self.assertEqual(report['linkInputCount'], 6)
        self.assertEqual(report['schemaVersion'], 2)
        self.assertEqual(report['linkManifestSHA256'], w.p.digest(self.manifest_file))
        for name, entry in self.policy['sources'].items():
            self.assertEqual((self.output / 'sources/windows' / entry['filename']).read_bytes(), self.original_sources[name])
        self.assertTrue((self.output / 'rebuild/scripts/mpv/windows_runtime.py').is_file())
        self.assertEqual(w._load(self.output / 'build-config/toolchain.json')['runtimeSourceStatus'], report['correspondingSourceStatus'])

    def test_real_map_anonymous_members_are_not_disk_identities(self):
        with patch.object(w, '_map_inputs', side_effect=AssertionError('map is not provenance')):
            self._recapture()
            self.complete()
        self.assertEqual((self.output / 'build-config/mpv-link.map').read_text(), REAL_MAP_EXCERPT)
        manifest = w._load(self.manifest_file)
        self.assertEqual({r['path'] for r in manifest['inputs']}, {str(f) for f in self.input_files})
        for record in manifest['inputs'][2:]:
            owner, version = self._fixed_owner(Path(record['path']))
            self.assertEqual((record['owner'], record['version']), (owner, version))
        self.assertIn('unselected members', manifest['inputScope'])
        self.assertIn('not source attribution', manifest['inputScope'])
        self.assertEqual(manifest['responseText'], '-dll synthetic response\n')
        self.assertIn('23.1.5', manifest['versionText'])
        self.assertEqual(manifest['reproducerSHA256'], w.p.digest(self.repro))
        self.assertFalse(list(self.output.rglob('*.tar')))
        # Review relies on the saved inventory; the large tar is not required.
        self.repro.unlink()
        self.assertEqual(w._review_link(self.output, self.arch, self.policy, self.packages)['inputs'], manifest['inputs'])

    def test_each_native_input_is_checked_for_real_owner(self):
        with patch.object(w, '_owner', side_effect=self._fixed_owner) as owner:
            w.capture_link_evidence(self.map, self.prefix, self.native, self.output)
        self.assertEqual([call.args[0] for call in owner.call_args_list], self.input_files[2:])

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

    def test_unknown_link_owner_even_with_rebound_manifest(self):
        def mutate(value):
            next(r for r in value['inputs'] if r['owner'] != 'project-fixed-source')['owner'] = 'unknown-package'
        self._change_manifest(mutate, rebind=True)
        self.assert_blocked()

    def test_missing_map(self):
        (self.output / 'build-config/mpv-link.map').unlink()
        self.assert_blocked()

    def test_changed_map(self):
        (self.output / 'build-config/mpv-link.map').write_text('missing original inputs')
        self.assert_blocked()

    def test_missing_reproducer_capture(self):
        self.repro.unlink()
        with self.assertRaises(FileNotFoundError):
            self._recapture()

    def test_missing_manifest_cannot_close_pending(self):
        self.manifest_file.unlink()
        self.assert_blocked()

    def test_legacy_map_only_evidence_cannot_close_pending(self):
        evidence = w._load(self.evidence_file)
        evidence['schemaVersion'] = 1
        evidence.pop('manifestSHA256')
        w.p.atomic_json(self.evidence_file, evidence)
        self.assert_blocked()

    def test_changed_manifest_input(self):
        self._change_manifest(lambda value: value['inputs'].pop())
        self.assert_blocked()

    def test_changed_manifest_response(self):
        self._change_manifest(lambda value: value.update(responseText='tampered'))
        self.assert_blocked()

    def test_changed_manifest_map_binding(self):
        self._change_manifest(lambda value: value.update(mapSHA256='0' * 64), rebind=True)
        self.assert_blocked()

    def test_changed_manifest_reproducer_binding(self):
        self._change_manifest(lambda value: value.update(reproducerSHA256='0' * 64), rebind=True)
        self.assert_blocked()

    def test_empty_input_manifest(self):
        self._change_manifest(lambda value: value.update(inputs=[]), rebind=True)
        self.assert_blocked()

    def test_missing_crt_manifest(self):
        self._change_manifest(lambda value: value.update(inputs=value['inputs'][:2]), rebind=True)
        self.assert_blocked()

    def test_changed_original_link_archive(self):
        (self.prefix / 'libproject.a').write_bytes(b'changed')
        self.assert_blocked()

    def test_changed_input_between_link_and_capture(self):
        self.input_files[0].write_bytes(b'wrong object')
        with self.assertRaisesRegex(ValueError, 'input bytes differ'):
            self._recapture()

    def test_tampered_reproducer_input(self):
        name, data = self.repro_members[-1]
        self.repro_members[-1] = (name, b'x' * len(data))
        self._write_repro()
        with self.assertRaisesRegex(ValueError, 'input bytes differ'):
            self._recapture()

    def test_unknown_capture_owner(self):
        with patch.object(w, '_owner', return_value=('unknown-package', '1')):
            with self.assertRaisesRegex(ValueError, 'Unknown link owner'):
                w.capture_link_evidence(self.map, self.prefix, self.native, self.output)

    def test_wrong_capture_package_version(self):
        owner, _ = self._fixed_owner(None)
        with patch.object(w, '_owner', return_value=(owner, 'wrong')):
            with self.assertRaisesRegex(ValueError, 'package version'):
                w.capture_link_evidence(self.map, self.prefix, self.native, self.output)

    def test_capture_crt_metadata_is_not_startup_evidence(self):
        note = self.native / 'crt-note.txt'
        note.write_bytes(b'CRT metadata')
        self.repro_members = self.repro_members[:4] + [
            ('final/' + note.as_posix().replace(':', '').lstrip('/'), note.read_bytes())]
        self._write_repro()
        with self.assertRaisesRegex(ValueError, 'Missing CRT'):
            self._recapture()

    def test_capture_requires_actual_crt_input(self):
        self.repro_members = self.repro_members[:4]
        self._write_repro()
        with self.assertRaisesRegex(ValueError, 'Missing CRT'):
            self._recapture()

    def test_capture_rejects_project_path_escape(self):
        outside = self.root / 'outside.o'
        outside.write_bytes(b'not a project object')
        self.repro_members.append(('final/' + outside.as_posix().replace(':', '').lstrip('/'), outside.read_bytes()))
        self._write_repro()
        with self.assertRaisesRegex(ValueError, 'outside fixed'):
            self._recapture()

    def test_capture_rejects_non_object_work_input(self):
        outside = self.root / 'work/unfixed.a'
        outside.write_bytes(b'archive outside project prefix')
        self.repro_members.append(('final/' + outside.as_posix().replace(':', '').lstrip('/'), outside.read_bytes()))
        self._write_repro()
        with self.assertRaisesRegex(ValueError, 'outside fixed'):
            self._recapture()

    def test_capture_rejects_unsafe_tar_paths(self):
        for name in ('/absolute.o', 'final/../escape.o', 'final//input.o',
                     'final/./input.o', 'final/D:/input.o', 'final/D\\input.o'):
            with self.subTest(name=name):
                self.repro_members.append((name, b'evil'))
                self._write_repro()
                with self.assertRaisesRegex(ValueError, 'Unsafe reproducer'):
                    self._recapture()
                self.repro_members.pop()

    def test_capture_rejects_nonregular_tar_members(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                self._write_repro()
                with tarfile.open(self.repro, 'a:') as container:
                    member = tarfile.TarInfo('final/untrusted')
                    member.type, member.linkname = kind, 'response.txt'
                    container.addfile(member)
                with self.assertRaisesRegex(ValueError, 'Invalid reproducer member'):
                    self._recapture()

    def test_capture_rejects_duplicate_or_different_root(self):
        for member in (self.repro_members[0], ('other/version.txt', b'another version')):
            with self.subTest(member=member):
                self.repro_members.append(member)
                self._write_repro()
                with self.assertRaisesRegex(ValueError, 'Invalid reproducer member'):
                    self._recapture()
                self.repro_members.pop()

    def test_capture_requires_response(self):
        self.repro_members.pop(0)
        self._write_repro()
        with self.assertRaisesRegex(ValueError, 'Missing reproducer'):
            self._recapture()

    def test_capture_accepts_native_coff_without_version_file(self):
        # CI 42177442531: LLD COFF writes response.txt without version.txt.
        self.repro_members.pop(1)
        self._write_repro()
        self._recapture()
        manifest = w._load(self.manifest_file)
        self.assertIsNone(manifest['versionText'])
        self.assertEqual(len(manifest['inputs']), 6)
        self.complete()

    def test_review_rejects_tar_path_escape_with_rebound_manifest(self):
        def mutate(value):
            value['inputs'][0]['tarMember'] = 'final/../probe.obj'
        self._change_manifest(mutate, rebind=True)
        self.assert_blocked()

    def test_review_rejects_changed_original_path_with_rebound_manifest(self):
        def mutate(value):
            value['inputs'][0]['path'] = str(self.native / 'dllcrt2.o')
        self._change_manifest(mutate, rebind=True)
        self.assert_blocked()

    def test_review_rejects_project_claim_for_native_input(self):
        def mutate(value):
            value['inputs'][-1]['owner'] = 'project-fixed-source'
        self._change_manifest(mutate, rebind=True)
        self.assert_blocked()

    def test_reproducer_windows_drive_and_posix_path_restoration(self):
        self.assertEqual(w._original_spelling('final/D/a/_temp/msys64/clang64/lib/dllcrt2.o'),
                         'D:/a/_temp/msys64/clang64/lib/dllcrt2.o')
        self.assertEqual(w._original_spelling('final/tmp/prefix/libproject.a'), '/tmp/prefix/libproject.a')

    def test_map_formats_ignore_symbols_and_keep_startup_objects(self):
        text = (' 0001:00000000 __entry 0000000140001000 f C:/clang64/lib/crt2.o\n'
                ' 0001:00000010 function 0000000140001010 f C:/clang64/lib/libc++.a:member.o\n'
                ' 0000:00000000 __ImageBase 0000000140000000 <absolute>\n')
        self.assertEqual(w._map_inputs(text), ['C:/clang64/lib/crt2.o', 'C:/clang64/lib/libc++.a'])
        with self.assertRaises(ValueError):
            w._map_inputs('no actual input table')


if __name__ == '__main__':
    unittest.main()
