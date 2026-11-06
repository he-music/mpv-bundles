import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/mpv'))
import build
import prepare
import smoke


class RecipeTests(unittest.TestCase):
    def test_pinned_versions_targets_and_https_digests(self):
        lock = json.loads((ROOT / 'scripts/mpv/sources.lock.json').read_text())
        self.assertEqual((lock['mpvVersion'], lock['clientAPI'], lock['sources']['ffmpeg']['version']), ('0.42.0', '2.6', '8.0.1'))
        self.assertEqual(set(prepare.TARGETS), {'darwin/universal', 'windows/amd64', 'windows/arm64', 'linux/amd64', 'linux/arm64'})
        for entry in lock['sources'].values():
            self.assertTrue(entry['url'].startswith('https://'))
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')

    def test_dst_decoder_and_hard_config_check(self):
        self.assertIn('dst', build.AUDIO_DECODERS)
        options = build.ffmpeg_options(Path('/tmp/prefix'), '', '')
        self.assertIn('--enable-decoder=' + ','.join(build.AUDIO_DECODERS), options)
        self.assertIn('CONFIG_DST_DECODER 1', (ROOT / 'scripts/mpv/build.py').read_text())

    def test_dsf_fmt_layout_is_parseable(self):
        with tempfile.TemporaryDirectory() as directory:
            file = smoke.samples(Path(directory))[1]
            data = file.read_bytes()
            self.assertEqual(data[:4], b'DSD ')
            self.assertEqual(data[28:32], b'fmt ')
            size = int.from_bytes(data[32:40], 'little')
            self.assertEqual(size, 52)
            version, format_id, channel_type, channels, rate, bits, count, block, reserved = __import__('struct').unpack_from('<IIIIIIQII', data, 40)
            self.assertEqual((version, format_id, channel_type, channels, rate, bits, block, reserved), (1, 0, 2, 2, 2822400, 1, 4096, 0))
            self.assertGreater(count, 0)

    def test_scripts_disabled_and_dst_sample_is_explicit(self):
        text = (ROOT / 'scripts/mpv/build.py').read_text()
        self.assertIn("'-Dlua=lua52'", text)
        self.assertNotIn("'-Dlua=disabled'", text)
        smoke_text = (ROOT / 'scripts/mpv/smoke.py').read_text()
        self.assertIn('codec != "dst"', smoke_text)
        self.assertIn('not-executed: no --dst-sample supplied', smoke_text)

    def test_workflow_release_and_platform_contracts(self):
        text = (ROOT / '.github/workflows/bundle.yml').read_text()
        for item in ('asset_name:', 'if-no-files-found: error', 'CLANG64', 'CLANGARM64', 'msys2 {0}', 'requirements-build.txt', 'GITHUB_SHA', 'ubuntu:22.04@sha256:', '--repo "${{ github.repository }}"', 'SHA256SUMS'):
            self.assertIn(item, text)
        self.assertNotIn('name: libmpv-${{ matrix.target }}', text)


if __name__ == '__main__':
    unittest.main()
