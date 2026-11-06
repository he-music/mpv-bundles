#!/usr/bin/env python3
"""将可信 schema 1 全量包重组为运行包及共享材料；不执行任何归档内容。"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"darwin/universal": "libmpv.2.dylib", "linux/amd64": "libmpv.so.2",
           "linux/arm64": "libmpv.so.2", "windows/amd64": "libmpv-2.dll",
           "windows/arm64": "libmpv-2.dll"}
FIELDS = ("mpvVersion", "ffmpegVersion", "clientAPI", "recipeSHA256")
WINDOWS_STATUS = "project-fixed-sources-and-windows-runtime-materials"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(files):
    result = {}
    for name, file in sorted(files.items()):
        if isinstance(file, bytes):
            result[name] = {"sha256": digest(file), "size": len(file)}
        else:
            h = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            result[name] = {"sha256": h.hexdigest(), "size": file.stat().st_size}
    return result


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))
            or "\\" in name or ":" in name):
        raise ValueError(f"不安全归档路径: {name}")
    return path


def unpack(file, destination):
    files, seen = {}, set()
    with tarfile.open(file, "r:gz") as archive:
        for entry in archive:
            name = entry.name.rstrip("/") if entry.isdir() else entry.name
            path = safe_name(name)
            if (path.parts[0] != "mpv" or name.casefold() in seen
                    or not (entry.isfile() or entry.isdir()) or entry.issparse()):
                raise ValueError("归档含重复、链接或特殊文件")
            seen.add(name.casefold())
            target = destination.joinpath(*path.parts)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            files[path.relative_to("mpv").as_posix()] = target
    manifest_file = files.pop("manifest.json", None)
    if manifest_file is None:
        raise ValueError("缺少原manifest")
    original = manifest_file.read_bytes()
    manifest = json.loads(original)
    if manifest.get("schemaVersion") != 1 or manifest.get("files") != inventory(files):
        raise ValueError("原归档inventory不一致")
    return files, manifest, original


def native(name, file, target):
    if "/" in name or not re.fullmatch(r"[^/]+(?:\.dll|\.dylib|\.so(?:\.[0-9]+)*)", name):
        return False
    with file.open("rb") as source:
        magic = source.read(4)
    if target.startswith("windows/"):
        return name.endswith(".dll") and magic[:2] == b"MZ"
    if target.startswith("linux/"):
        return ".so" in name and magic == b"\x7fELF"
    return name.endswith(".dylib") and magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf")


def pack(destination, root, files, manifest):
    all_files = {**files, "manifest.json": encoded(manifest)}
    # 文件和目录均固定元数据，gzip不记录本地文件名或时间。
    with destination.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, file in sorted(all_files.items()):
                safe_name(name)
                info = tarfile.TarInfo(f"{root}/{name}")
                info.mode = 0o644
                info.size = len(file) if isinstance(file, bytes) else file.stat().st_size
                if isinstance(file, bytes):
                    archive.addfile(info, io.BytesIO(file))
                else:
                    with file.open("rb") as stream:
                        archive.addfile(info, stream)
    destination.chmod(0o644)


def asset(target, version):
    return f"libmpv-{target.replace('/', '-')}-{version}.tar.gz"


def split(version, input_dir, output_dir, expected_source_manifest_sha256):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+", version):
        raise ValueError("版本格式必须是 v<mpv版本>-<修订号>")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_source_manifest_sha256):
        raise ValueError("必须提供可信来源manifest SHA256")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("输出目录必须为空，不允许旧资源或临时残留")
    original_index = (input_dir / "manifest.json").read_bytes()
    if digest(original_index) != expected_source_manifest_sha256:
        raise ValueError("来源manifest SHA256不匹配")
    index = json.loads(original_index)
    if index.get("schemaVersion") != 1 or set(index.get("targets", {})) != set(TARGETS):
        raise ValueError("来源必须是五目标完整schema 1")
    if not version.startswith(f"v{index['mpvVersion']}-"):
        raise ValueError("发布版本必须保持来源mpv版本")
    expected_names = {"manifest.json", "SHA256SUMS"} | {asset(t, index['bundleVersion']) for t in TARGETS}
    if {p.name for p in input_dir.iterdir()} != expected_names or any(not p.is_file() or p.is_symlink() for p in input_dir.iterdir()):
        raise ValueError("来源目录含额外、缺失或非普通文件")
    repository = index.get("repository")
    if repository != "he-music/mpv-bundles":
        raise ValueError("来源repository不受支持")
    base_url = f"https://github.com/{repository}/releases/download/{version}/"
    origin = {"bundleVersion": index['bundleVersion'], "manifestSHA256": digest(original_index)}
    result = {k: index[k] for k in (*FIELDS, "repository")}
    result.update(schemaVersion=2, bundleVersion=version, origin=origin, targets={})
    with tempfile.TemporaryDirectory(prefix="mpv-split-") as work:
        work = Path(work)
        materials, mappings, runtimes = {}, {}, {}
        def add(name, file):
            # 同名不同内容必须显式拒绝，不能由目标迭代顺序决定结果。
            if name in materials and inventory({name: materials[name]}) != inventory({name: file}):
                raise ValueError(f"共享材料同名内容冲突: {name}")
            materials[name] = file
        for target, library in sorted(TARGETS.items()):
            entry = index['targets'][target]
            filename = asset(target, index['bundleVersion'])
            if entry.get('asset') != filename or entry.get('root') != 'mpv' or entry.get('library') != library:
                raise ValueError("来源目标metadata不一致")
            if entry.get('url') != f"https://github.com/{repository}/releases/download/{index['bundleVersion']}/{filename}":
                raise ValueError("来源URL不一致")
            path = input_dir / filename
            if inventory({filename: path})[filename] != {k: entry[k] for k in ('sha256', 'size')}:
                raise ValueError("来源archive size/hash不一致")
            files, manifest, original = unpack(path, work / target.replace('/', '-'))
            if digest(original) != entry.get('manifestSHA256'):
                raise ValueError("来源包manifest SHA256不一致")
            if manifest.get('target') != target or any(manifest.get(k) != index.get(k) for k in FIELDS):
                raise ValueError("来源core metadata不一致")
            status = manifest.get('details', {}).get('correspondingSourceStatus')
            if (status != entry.get('correspondingSourceStatus') or
                    status != (WINDOWS_STATUS if target.startswith('windows/') else 'project-fixed-sources')):
                raise ValueError("来源source status pending或不一致")
            dependencies = manifest.get('dependencies')
            if not isinstance(dependencies, dict) or library not in dependencies:
                raise ValueError("缺少运行依赖inventory")
            binaries = {}
            for name, imports in dependencies.items():
                if not isinstance(imports, list) or not all(isinstance(s, str) for s in imports):
                    raise ValueError("依赖列表无效")
                if name not in files or not native(name, files[name], target):
                    raise ValueError("依赖keys必须是root原生动态库")
                binaries[name] = files[name]
            if target.startswith('windows/') and 'libiconv-2.dll' not in binaries:
                raise ValueError("Windows缺少libiconv-2.dll")
            licenses = {n: f for n, f in files.items() if n.startswith('licenses/')}
            if not licenses or any(name in licenses for name in ('licenses/SOURCE-MATERIALS.json', 'licenses/NOTICE.txt')):
                raise ValueError("来源licenses无效")
            mapping = {"sources": {}, "rebuild": {}, "targetFiles": {}, "excludedLogs": [],
                       "originalManifestSHA256": digest(original)}
            target_root = 'targets/' + target.replace('/', '-')
            add(target_root + '/original-manifest.json', original)
            for name, file in sorted(files.items()):
                if name in binaries or name in licenses:
                    continue
                if native(name, file, target):
                    raise ValueError("存在未在dependencies登记的原生运行库")
                if name.endswith('.log') or PurePosixPath(name).name == 'meson-log.txt':
                    mapping['excludedLogs'].append(name)
                    continue
                if name.startswith('sources/'):
                    destination = 'sources/' + PurePosixPath(name).name
                    kind = 'sources'
                elif name.startswith('rebuild/'):
                    destination, kind = name, 'rebuild'
                else:
                    destination, kind = target_root + '/' + name, 'targetFiles'
                add(destination, file)
                mapping[kind][name] = destination
            if not mapping['sources'] or not mapping['rebuild']:
                raise ValueError("缺少源码或重建入口")
            mappings[target] = mapping
            runtimes[target] = (binaries, licenses, manifest)
        if len([n for n in materials if n.startswith('sources/')]) != 14:
            raise ValueError("共享源码必须含14个去重原始归档")
        materials['packaging/split_release.py'] = Path(__file__).read_bytes()
        materials['packaging/LICENSE'] = (ROOT / 'LICENSE').read_bytes()
        materials['README.md'] = ("# 重建材料\n\n共享 sources/ 保存原始源归档，rebuild/ 保存原始重建入口。\n"
            "manifest.json 的 targets 映射记录每个目标原路径到共享文件或 targets/<id>/ 配置的位置；"
            "重建时按映射恢复原 mpv/ 布局，再遵循 rebuild/ 中的脚本和目标 commands/options。\n"
            "日志按 excludedLogs 排除；原 licenses/ 在同版本运行包中。\n"
            "Windows 工具链材料仅含原包的配方、notice和评审，不声称包含全部 LLVM 源码。\n"
            "packaging/ 仅记录本次重组方法，与原 core recipeSHA256 分开。\n").encode()
        materials_manifest = {"schemaVersion": 1, "packageKind": "materials", "bundleVersion": version,
                              "origin": origin, "targets": mappings, "files": inventory(materials)}
        materials_name = f'libmpv-materials-{version}.tar.gz'
        materials_path = work / materials_name
        pack(materials_path, 'materials', materials, materials_manifest)
        pointer = {"asset": materials_name, "url": base_url + materials_name, "root": 'materials',
                   **inventory({materials_name: materials_path})[materials_name],
                   "manifestSHA256": digest(encoded(materials_manifest))}
        result['materials'] = pointer
        for target, (binaries, licenses, original) in runtimes.items():
            notice = {"schemaVersion": 1, "materials": pointer, "origin": origin,
                      "rebuild": {"root": 'rebuild', "target": 'targets/' + target.replace('/', '-'),
                                  "mapping": 'manifest.json#targets/' + target}}
            licenses = {**licenses, 'licenses/SOURCE-MATERIALS.json': encoded(notice)}
            licenses = {**licenses, 'licenses/NOTICE.txt': (
                'This distribution contains libmpv and its linked third-party libraries.\n'
                'Applicable copyright and license texts accompany these files.\n'
                'libmpv remains independently replaceable under the applicable LGPL terms.\n'
                'Portions of this software are copyright © 2025 The FreeType Project\n'
                '(https://freetype.org). All rights reserved.\n'
                'Corresponding sources, patches and rebuild instructions for this version:\n'
                + pointer['url'] + '\nSee SOURCE-MATERIALS.json for fixed hashes and source provenance.\n'
            ).encode('utf-8')}
            files = {**binaries, **licenses}
            runtime_manifest = {k: original[k] for k in ('schemaVersion', *FIELDS, 'target', 'dependencies')}
            runtime_manifest.update(files=inventory(files), details={"packageKind": "runtime", "materials": pointer,
                "origin": origin, "correspondingSourceStatus": original['details']['correspondingSourceStatus']})
            filename = asset(target, version)
            pack(work / filename, 'mpv', files, runtime_manifest)
            result['targets'][target] = {"asset": filename, "url": base_url + filename, "root": 'mpv',
                "library": TARGETS[target], **inventory({filename: work / filename})[filename],
                "manifestSHA256": digest(encoded(runtime_manifest)),
                "correspondingSourceStatus": original['details']['correspondingSourceStatus'],
                "runtimeFiles": inventory(binaries), "licenseFiles": inventory(licenses)}
        (work / 'manifest.json').write_bytes(encoded(result))
        names = sorted([materials_name, 'manifest.json', *(asset(t, version) for t in TARGETS)])
        (work / 'SHA256SUMS').write_text(''.join(f"{inventory({n: work / n})[n]['sha256']}  {n}\n" for n in names))
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in [*names, 'SHA256SUMS']:
            shutil.copyfile(work / name, output_dir / name)
            (output_dir / name).chmod(0o644)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--expected-source-manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        split(args.version, args.input_dir, args.output_dir, args.expected_source_manifest_sha256)
    except (ValueError, KeyError, OSError, tarfile.TarError) as error:
        parser.exit(1, f"拆包失败: {error}\n")


if __name__ == '__main__':
    main()
