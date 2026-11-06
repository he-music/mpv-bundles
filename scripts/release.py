#!/usr/bin/env python3
"""为各平台生成独立发布资源和固定版本下载清单，不执行归档内容。"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/mpv"))
import prepare


def version_string(value):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+", value):
        raise ValueError("版本格式必须是 v<mpv版本>-<配方修订号>，例如 v0.42.0-1")
    return value


def asset_name(target, version):
    version_string(version)
    if target not in prepare.TARGETS:
        raise ValueError("不支持的发布目标")
    return f"libmpv-{target.replace('/', '-')}-{version}.tar.gz"


def package(target, version, output, cache):
    version_string(version)
    directory = cache / "prepared" / target.replace("/", "-") / "mpv"
    manifest = prepare.verify(directory, target)
    if not version.startswith(f"v{manifest['mpvVersion']}-"):
        raise ValueError("发布版本与库版本不一致")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / asset_name(target, version)
    # 元数据固定；同一已验证目录再次打包产生相同的归档摘要。
    with tempfile.NamedTemporaryFile(dir=output, suffix=".partial", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with temporary.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for file in [directory, *sorted(directory.rglob("*"))]:
                    if file.is_symlink():
                        raise ValueError("发布资源中不能包含软链接")
                    name = "mpv" if file == directory else "mpv/" + file.relative_to(directory).as_posix()
                    info = archive.gettarinfo(str(file), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    info.pax_headers = {}
                    if file.is_file():
                        with file.open("rb") as source:
                            archive.addfile(info, source)
                    else:
                        archive.addfile(info)
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def archive_manifest(file):
    with tarfile.open(file, "r:gz") as archive:
        entries = archive.getmembers()
        seen = set()
        for entry in entries:
            path = PurePosixPath(entry.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "mpv"
                    or "\\" in entry.name or ":" in entry.name or not (entry.isdir() or entry.isfile())
                    or entry.name.casefold() in seen):
                raise ValueError("发布归档结构无效")
            seen.add(entry.name.casefold())
        stream = archive.extractfile("mpv/manifest.json")
        if stream is None:
            raise ValueError("缺少包内清单")
        with stream:
            manifest = json.load(stream)
        expected = manifest.get("files")
        if not isinstance(expected, dict):
            raise ValueError("包内文件清单无效")
        actual = {}
        for entry in entries:
            if not entry.isfile() or entry.name == "mpv/manifest.json":
                continue
            with archive.extractfile(entry) as source:
                digest = hashlib.sha256()
                size = 0
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                actual[entry.name.removeprefix("mpv/")] = {"sha256": digest.hexdigest(), "size": size}
        if actual != expected:
            raise ValueError("归档文件与包内清单不一致")
        return manifest


def archive_manifest_digest(file):
    with tarfile.open(file, "r:gz") as archive:
        with archive.extractfile("mpv/manifest.json") as source:
            return hashlib.sha256(source.read()).hexdigest()


def index(version, directory):
    version_string(version)
    lock = json.loads(prepare.LOCK.read_text())
    if not version.startswith(f"v{lock['mpvVersion']}-"):
        raise ValueError("发布版本与源码锁不一致")
    result = {"schemaVersion": 1, "bundleVersion": version, "repository": "he-music/mpv-bundles",
              "mpvVersion": lock["mpvVersion"], "ffmpegVersion": lock["sources"]["ffmpeg"]["version"],
              "clientAPI": lock["clientAPI"], "recipeSHA256": prepare.recipe_digest(), "targets": {}}
    expected = {asset_name(target, version) for target in prepare.TARGETS}
    actual = {file.name for file in directory.glob("*.tar.gz")}
    if expected != actual:
        raise ValueError(f"发布资源不完整或含额外文件: 缺少{sorted(expected - actual)}, 额外{sorted(actual - expected)}")
    for target in sorted(prepare.TARGETS):
        file = directory / asset_name(target, version)
        manifest = archive_manifest(file)
        for key in ("mpvVersion", "ffmpegVersion", "clientAPI", "recipeSHA256"):
            if manifest.get(key) != result[key]:
                raise ValueError(f"{target}: {key} 与当前发布配方不一致")
        if manifest.get("target") != target:
            raise ValueError("归档目标与资源名不一致")
        result["targets"][target] = {
            "asset": file.name, "sha256": prepare.digest(file), "size": file.stat().st_size,
            "manifestSHA256": archive_manifest_digest(file),
            "root": "mpv", "library": prepare.LIBRARIES[target.split("/")[0]],
            "url": f"https://github.com/he-music/mpv-bundles/releases/download/{version}/{file.name}",
            "correspondingSourceStatus": manifest.get("details", {}).get("correspondingSourceStatus", "not-recorded"),
        }
    prepare.atomic_json(directory / "manifest.json", result)
    files = [directory / name for name in sorted(expected)] + [directory / "manifest.json"]
    (directory / "SHA256SUMS").write_text(
        "".join(f"{prepare.digest(file)}  {file.name}\n" for file in files), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["package", "index"])
    parser.add_argument("--version", required=True, type=version_string)
    parser.add_argument("--target", choices=sorted(prepare.TARGETS))
    parser.add_argument("--cache", type=Path, default=ROOT / "build/mpv-cache")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.action == "package":
        if not args.target:
            parser.error("package 需要 --target")
        print(package(args.target, args.version, args.output_dir, args.cache))
    else:
        print(json.dumps(index(args.version, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
