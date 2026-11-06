"""Synthetic headers only: these tests do not constitute native runtime validation."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('split_release', Path(__file__).resolve().parents[1] / 'scripts/split_release.py')
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


class SplitReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.output = self.root / 'output'
        self.index = dict(schemaVersion=1, bundleVersion='v0.42.0-1', repository='he-music/mpv-bundles',
                          mpvVersion='0.42.0', ffmpegVersion='8.0.1', clientAPI='2.6', recipeSHA256='1' * 64, targets={})
        self.originals = {}
        self.manifests = {}
        for target, library in s.TARGETS.items():
            magic = b'MZ00' if target.startswith('windows/') else b'\x7fELF'
            if target.startswith('darwin/'):
                magic = b'\xca\xfe\xba\xbe'
            files = {library: magic + target.encode(), 'licenses/mpv/LICENSE': b'original license',
                     'rebuild/scripts/build.py': b'original rebuild', 'rebuild/patches/fix.patch': b'patch',
                     'sources.lock.json': b'lock', 'build-config/commands.json': b'commands',
                     'build-config/options.json': b'options', 'build-config/config.h': b'config',
                     'build-config/toolchain.json': b'notice only', 'build-config/meson-log.txt': b'long log',
                     'build-config/compile.log': b'long log', 'validation.json': b'original validation'}
            for number in range(14 if target.startswith('windows/') else 11):
                files[f'sources/{number:02}.tar.gz'] = f'original source {number}'.encode()
            dependencies = {library: []}
            status = 'project-fixed-sources'
            if target.startswith('windows/'):
                files['libiconv-2.dll'] = b'MZiconv' + target.encode()
                files['build-config/mpv-link.map'] = b'link map'
                files['windows-runtime-review.json'] = b'license review'
                files['build-config/windows-link-reproducer.json'] = b'link inventory'
                dependencies['libiconv-2.dll'] = []
                dependencies[library] = ['libiconv-2.dll']
                status = s.WINDOWS_STATUS
            manifest = {k: self.index[k] for k in s.FIELDS}
            manifest.update(schemaVersion=1, target=target, dependencies=dependencies,
                            details={'correspondingSourceStatus': status}, files=s.inventory(files))
            self.originals[target] = files
            self.manifests[target] = manifest
            self.repack(target)
        self.save_index()
        (self.source / 'SHA256SUMS').write_text('fixture: not the trust anchor\n')

    def repack(self, target):
        manifest = self.manifests[target]
        name = s.asset(target, self.index['bundleVersion'])
        s.pack(self.source / name, 'mpv', self.originals[target], manifest)
        self.index['targets'][target] = dict(asset=name, root='mpv', library=s.TARGETS[target],
            url=f"https://github.com/he-music/mpv-bundles/releases/download/{self.index['bundleVersion']}/{name}",
            manifestSHA256=s.digest(s.encoded(manifest)),
            correspondingSourceStatus=manifest['details']['correspondingSourceStatus'],
            **s.inventory({name: self.source / name})[name])

    def save_index(self):
        (self.source / 'manifest.json').write_bytes(s.encoded(self.index))
        self.trusted = s.digest(s.encoded(self.index))

    def run_split(self, output=None, trusted=None):
        return s.split('v0.42.0-2', self.source, output or self.output, trusted or self.trusted)

    def read_archive(self, path, root):
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            for entry in members:
                self.assertTrue(entry.isfile())
                self.assertEqual((entry.mode, entry.mtime, entry.uid, entry.gid, entry.uname, entry.gname),
                                 (0o644, 0, 0, 0, '', ''))
            return {e.name.removeprefix(root + '/'): archive.extractfile(e).read() for e in members}

    def test_native_workflow_can_split_unpublished_same_version_inputs(self):
        before = (self.source / 'manifest.json').read_bytes()
        result = s.split('v0.42.0-1', self.source, self.output, self.trusted)
        self.assertEqual(result['schemaVersion'], 2)
        self.assertEqual(result['bundleVersion'], result['origin']['bundleVersion'])
        self.assertEqual((self.source / 'manifest.json').read_bytes(), before)

    def test_deterministic_assets_binary_license_bytes_and_schema_binding(self):
        result = self.run_split()
        self.assertEqual(result['schemaVersion'], 2)
        self.assertEqual(result['recipeSHA256'], self.index['recipeSHA256'])
        self.assertEqual(result['origin'], {'bundleVersion': 'v0.42.0-1', 'manifestSHA256': self.trusted})
        self.assertEqual(len(list(self.output.iterdir())), 8)
        self.run_split(self.root / 'repeat')
        for file in self.output.iterdir():
            self.assertEqual(file.read_bytes(), (self.root / 'repeat' / file.name).read_bytes())
            self.assertEqual(file.stat().st_mode & 0o777, 0o644)
        sums = (self.output / 'SHA256SUMS').read_text().splitlines()
        self.assertEqual(len(sums), 7)
        for line in sums:
            digest, name = line.split('  ')
            self.assertEqual(s.digest((self.output / name).read_bytes()), digest)
            self.assertNotEqual(name, 'SHA256SUMS')
        pointer = result['materials']
        self.assertEqual(s.inventory({pointer['asset']: self.output / pointer['asset']})[pointer['asset']],
                         {k: pointer[k] for k in ('sha256', 'size')})
        for target, entry in result['targets'].items():
            files = self.read_archive(self.output / entry['asset'], 'mpv')
            manifest_bytes = files.pop('manifest.json')
            manifest = json.loads(manifest_bytes)
            self.assertEqual(s.digest(manifest_bytes), entry['manifestSHA256'])
            self.assertEqual(s.inventory(files), manifest['files'])
            self.assertEqual(manifest['files'], entry['runtimeFiles'] | entry['licenseFiles'])
            self.assertFalse(set(entry['runtimeFiles']) & set(entry['licenseFiles']))
            self.assertEqual(set(entry['runtimeFiles']), set(self.manifests[target]['dependencies']))
            self.assertEqual(manifest['details']['materials'], pointer)
            for name in entry['runtimeFiles']:
                self.assertNotIn('/', name)
                self.assertEqual(files[name], self.originals[target][name])
            for name in entry['licenseFiles']:
                self.assertTrue(name.startswith('licenses/'))
                if name not in ('licenses/SOURCE-MATERIALS.json', 'licenses/NOTICE.txt'):
                    self.assertEqual(files[name], self.originals[target][name])
            self.assertIn(b'The FreeType Project', files['licenses/NOTICE.txt'])
            self.assertIn(pointer['url'].encode(), files['licenses/NOTICE.txt'])
            notice = json.loads(files['licenses/SOURCE-MATERIALS.json'])
            self.assertEqual(notice['materials'], pointer)
            self.assertEqual(notice['origin'], result['origin'])
            self.assertEqual(set(files), set(entry['runtimeFiles']) | set(entry['licenseFiles']))

    def test_materials_mapping_inventory_and_deduplication(self):
        result = self.run_split()
        materials = self.read_archive(self.output / result['materials']['asset'], 'materials')
        raw = materials.pop('manifest.json')
        manifest = json.loads(raw)
        self.assertEqual(s.digest(raw), result['materials']['manifestSHA256'])
        self.assertEqual(manifest['files'], s.inventory(materials))
        self.assertEqual(len([n for n in materials if n.startswith('sources/')]), 14)
        self.assertEqual(len([n for n in materials if n.startswith('rebuild/')]), 2)
        self.assertEqual(materials['packaging/split_release.py'], Path(s.__file__).read_bytes())
        self.assertEqual(materials['packaging/LICENSE'], (s.ROOT / 'LICENSE').read_bytes())
        self.assertFalse(any(n.endswith(('.dll', '.dylib', '.so.2', '.log', 'meson-log.txt')) for n in materials))
        for target, mapping in manifest['targets'].items():
            original = self.originals[target]
            accounted = set()
            for kind in ('sources', 'rebuild', 'targetFiles'):
                for old, new in mapping[kind].items():
                    self.assertEqual(original[old], materials[new])
                    accounted.add(old)
            self.assertEqual(set(original), accounted | set(mapping['excludedLogs']) |
                             set(self.manifests[target]['dependencies']) | {n for n in original if n.startswith('licenses/')})
            raw_original = materials['targets/' + target.replace('/', '-') + '/original-manifest.json']
            self.assertEqual(s.digest(raw_original), mapping['originalManifestSHA256'])
            self.assertEqual(json.loads(raw_original), self.manifests[target])

    def test_reject_untrusted_hash_missing_targets_and_schema(self):
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.run_split(trusted='0' * 64)
        self.index['schemaVersion'] = 2
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'schema 1'):
            self.run_split()
        self.index['schemaVersion'] = 1
        self.index['targets'].pop('linux/amd64')
        self.save_index()
        with self.assertRaisesRegex(ValueError, '五目标'):
            self.run_split()

    def test_reject_archive_hash_and_size(self):
        entry = self.index['targets']['linux/amd64']
        entry['size'] += 1
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'size/hash'):
            self.run_split()

    def test_reject_original_manifest_digest(self):
        self.index['targets']['linux/amd64']['manifestSHA256'] = '0' * 64
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'manifest SHA256'):
            self.run_split()

    def test_reject_metadata_and_pending(self):
        target = 'darwin/universal'
        self.manifests[target]['recipeSHA256'] = '9' * 64
        self.repack(target)
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'core metadata'):
            self.run_split()
        self.manifests[target]['recipeSHA256'] = self.index['recipeSHA256']
        self.manifests[target]['details']['correspondingSourceStatus'] = 'pending'
        self.repack(target)
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.run_split()

    def test_reject_inventory_mismatch(self):
        self.originals['darwin/universal']['build-info.json'] = b'unlisted'
        self.repack('darwin/universal')
        self.save_index()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.run_split()

    def test_reject_source_and_rebuild_conflicts(self):
        target = 'linux/amd64'
        for name in ('sources/00.tar.gz', 'rebuild/scripts/build.py'):
            with self.subTest(name=name):
                previous = self.originals[target][name]
                self.originals[target][name] = b'conflict'
                self.manifests[target]['files'] = s.inventory(self.originals[target])
                self.repack(target)
                self.save_index()
                with self.assertRaisesRegex(ValueError, '冲突'):
                    self.run_split()
                self.originals[target][name] = previous

    def test_reject_output_residue_and_input_extras(self):
        self.output.mkdir()
        (self.output / 'old.partial').write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, '残留'):
            self.run_split()
        (self.source / 'extra.partial').write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, '额外'):
            self.run_split(self.root / 'clean')

    def test_reject_unsafe_archive_members(self):
        cases = [('mpv/../escape', tarfile.REGTYPE), ('/mpv/escape', tarfile.REGTYPE),
                 ('mpv/a\\b', tarfile.REGTYPE), ('mpv/a:b', tarfile.REGTYPE),
                 ('mpv/symlink', tarfile.SYMTYPE), ('mpv/hardlink', tarfile.LNKTYPE),
                 ('mpv/fifo', tarfile.FIFOTYPE), ('mpv/device', tarfile.CHRTYPE),
                 ('mpv/a/./b', tarfile.REGTYPE)]
        for number, (name, kind) in enumerate(cases):
            with self.subTest(name=name):
                path = self.root / f'unsafe{number}.tar.gz'
                with tarfile.open(path, 'w:gz') as archive:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    info.linkname = 'outside'
                    archive.addfile(info, io.BytesIO(b''))
                with self.assertRaises(ValueError):
                    s.unpack(path, self.root / f'unsafe{number}')
        path = self.root / 'duplicate.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name in ('mpv/a', 'mpv/A'):
                archive.addfile(tarfile.TarInfo(name), io.BytesIO(b''))
        with self.assertRaisesRegex(ValueError, '重复'):
            s.unpack(path, self.root / 'duplicate')

    def test_reject_dependency_metadata_files_non_native_and_missing_iconv(self):
        target = 'windows/amd64'
        original = self.manifests[target]['dependencies'].copy()
        for dependencies in ({'libmpv-2.dll': []}, {**original, 'build-config/config.h': []},
                             {**original, 'licenses/mpv/LICENSE': []}):
            self.manifests[target]['dependencies'] = dependencies
            self.repack(target)
            self.save_index()
            with self.assertRaises(ValueError):
                self.run_split()
        self.manifests[target]['dependencies'] = original
        self.originals[target]['libmpv-2.dll'] = b'not a native library'
        self.manifests[target]['files'] = s.inventory(self.originals[target])
        self.repack(target)
        self.save_index()
        with self.assertRaisesRegex(ValueError, '原生'):
            self.run_split()


if __name__ == '__main__':
    unittest.main()
