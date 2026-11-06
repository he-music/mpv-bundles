import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/mpv"))
import prepare
import release


class WorkflowTests(unittest.TestCase):
    def test_workflow_parses_and_targets_have_correct_tools(self):
        flow = yaml.safe_load((ROOT / ".github/workflows/bundle.yml").read_text())
        self.assertIn("workflow_dispatch", flow["on"])
        rows = flow["jobs"]["build"]["strategy"]["matrix"]["include"]
        self.assertEqual({row["target"] for row in rows}, prepare.TARGETS)
        for row in rows:
            self.assertNotIn("/", row["asset_name"])
            if row["target"].startswith("windows/"):
                self.assertTrue(row["mingw"].startswith("mingw-w64-clang-"))
                self.assertIn(row["msystem"], ("CLANG64", "CLANGARM64"))
        draft = flow["jobs"]["release-draft"]
        self.assertIn("refs/tags/v", draft["if"])
        self.assertTrue(any(step.get("uses", "").startswith("actions/checkout@") for step in draft["steps"]))


class IntegrityTests(unittest.TestCase):
    def test_tar_mtime_preserved_for_generated_autotools_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name, mtime in (("configure.ac", 100), ("configure", 200)):
                    info = tarfile.TarInfo("source/" + name)
                    info.mtime = mtime
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b"x"))
            source = prepare.extract(archive, root / "extracted", "source")
            self.assertLess((source / "configure.ac").stat().st_mtime, (source / "configure").stat().st_mtime)

    def test_inventory_only_excludes_root_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifest.json").write_text("root")
            (root / "sources").mkdir()
            (root / "sources/manifest.json").write_text("nested")
            self.assertEqual(set(prepare.inventory(root)), {"sources/manifest.json"})

    def test_path_traversal_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("source/../../escape")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                prepare.extract(archive, root / "out", "source")
            self.assertFalse((root / "escape").exists())


class ReleaseTests(unittest.TestCase):
    def fixture(self, root, target):
        directory = root / "prepared" / target.replace("/", "-") / "mpv"
        directory.mkdir(parents=True)
        library = prepare.LIBRARIES[target.split("/")[0]]
        (directory / library).write_bytes(b"test library")
        manifest = {"schemaVersion": 1, "mpvVersion": "0.42.0", "ffmpegVersion": "8.0.1",
                    "clientAPI": "2.6", "recipeSHA256": prepare.recipe_digest(), "target": target,
                    "files": prepare.inventory(directory), "details": {"correspondingSourceStatus": "project-fixed-sources-and-windows-runtime-materials" if target.startswith("windows/") else "project-fixed-sources"}}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_package_is_repeatable_and_index_checks_actual_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "dist"
            expected_manifests = {}
            for target in sorted(prepare.TARGETS):
                manifest = self.fixture(root, target)
                expected_manifests[target] = hashlib.sha256(json.dumps(manifest).encode()).hexdigest()
                with patch.object(prepare, "verify", return_value=manifest):
                    archive = release.package(target, "v0.42.0-1", output, root)
                    digest = prepare.digest(archive)
                    self.assertEqual(archive.stat().st_mode & 0o044, 0o044)
                    release.package(target, "v0.42.0-1", output, root)
                    self.assertEqual(prepare.digest(archive), digest)
            result = release.index("v0.42.0-1", output)
            self.assertEqual(set(result["targets"]), prepare.TARGETS)
            self.assertEqual({target: item["manifestSHA256"] for target, item in result["targets"].items()}, expected_manifests)
            lines = (output / "SHA256SUMS").read_text().splitlines()
            self.assertEqual(len(lines), 6)
            self.assertFalse(any(line.endswith("  SHA256SUMS") for line in lines))
            for line in lines:
                digest, file = line.split("  ")
                self.assertEqual(prepare.digest(output / file), digest)

    def test_index_rejects_pending_windows_materials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "dist"
            for target in sorted(prepare.TARGETS):
                manifest = self.fixture(root, target)
                if target.startswith("windows/"):
                    manifest["details"]["correspondingSourceStatus"] = "pending-toolchain-runtime-review"
                    directory = root / "prepared" / target.replace("/", "-") / "mpv"
                    (directory / "manifest.json").write_text(json.dumps(manifest))
                with patch.object(prepare, "verify", return_value=manifest):
                    release.package(target, "v0.42.0-1", output, root)
            with self.assertRaisesRegex(ValueError, "Windows"):
                release.index("v0.42.0-1", output)

    def test_index_rejects_missing_target(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                release.index("v0.42.0-1", Path(temp))

    def test_index_rejects_wrong_recipe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "dist"
            for target in sorted(prepare.TARGETS):
                manifest = self.fixture(root, target)
                manifest["recipeSHA256"] = "0" * 64
                directory = root / "prepared" / target.replace("/", "-") / "mpv"
                (directory / "manifest.json").write_text(json.dumps(manifest))
                with patch.object(prepare, "verify", return_value=manifest):
                    release.package(target, "v0.42.0-1", output, root)
            with self.assertRaises(ValueError):
                release.index("v0.42.0-1", output)

    def test_release_version_cannot_contain_path_or_shell(self):
        for value in ("latest", "../v0.42.0-1", "v0.42.0-1;pwd", "v0.42.0"):
            with self.assertRaises(ValueError):
                release.version_string(value)


if __name__ == "__main__":
    unittest.main()
