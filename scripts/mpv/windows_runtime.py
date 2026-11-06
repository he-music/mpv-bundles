"""Close Windows runtime materials only against fixed bytes and final-link evidence.

Source archives are copied intact: legitimate source symlinks are never extracted.
This records provenance and rebuild materials, not a legal or reproducibility verdict.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tarfile

import prepare as p
from pe import imports, is_system_dll

POLICY = Path(__file__).with_name('windows-runtimes.lock.json')


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _within(path, root):
    return path == root or root in path.parents


def _map_inputs(text):
    """Read input columns of LLD's GNU and COFF /map table formats.

    GNU rows have Address Size Align Out In Symbol columns; COFF symbol rows
    end in Lib:Object. Ignore symbols and output sections, not unknown files.
    """
    found = set()
    for line in text.splitlines():
        gnu = re.match(r'^\s*[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+\d+\s+(.+?)\s*$', line)
        coff = re.match(r'^\s*[0-9a-fA-F]{4}:[0-9a-fA-F]+\s+\S+\s+[0-9a-fA-F]+\s+(?:[fi]\s+)*(.+?)\s*$', line)
        column = gnu.group(1) if gnu else coff.group(1) if coff else None
        if not column:
            continue
        # Archive members are represented either as a(member) or a:member.
        archive = re.fullmatch(r'(.+?\.(?:a|lib))(?:\([^\r\n]+\)|:[^\r\n]+)(?::\([^\r\n]*\))?', column, re.I)
        obj = re.fullmatch(r'(.+?\.(?:o|obj))(?::\([^\r\n]*\))?', column, re.I)
        if archive:
            found.add(archive.group(1).strip('"').replace('\\', '/'))
        elif obj:
            found.add(obj.group(1).strip('"').replace('\\', '/'))
    if not found:
        raise ValueError('Missing actual archive/object inputs in LLD map')
    return sorted(found)


def _native_path(value, project_root):
    if value.startswith('/') and os.name == 'nt':
        value = p.run(['cygpath', '-m', value], capture=True).strip()
    path = Path(value)
    return (path if path.is_absolute() else project_root / path).resolve()


def _owner(path):
    posix = p.run(['cygpath', '-u', str(path)], capture=True).strip()
    owners = p.run(['pacman', '-Qoq', posix], capture=True).splitlines()
    if len(owners) != 1 or not owners[0].strip():
        raise ValueError(f'Ambiguous package owner: {path}')
    owner = owners[0].strip()
    package = p.run(['pacman', '-Q', owner], capture=True).split()
    if len(package) != 2 or package[0] != owner:
        raise ValueError(f'Missing package version: {owner}')
    return owner, package[1]


def _package_check(owner, version, policy, arch):
    prefix = policy['architectures'][arch]['packagePrefix']
    suffix = owner.removeprefix(prefix)
    if not owner.startswith(prefix) or policy['packageVersions'].get(suffix) != version:
        raise ValueError(f'Unknown link owner or package version: {owner} {version}')


def capture_link_evidence(map_file, prefix, native_prefix, output):
    """Capture immediately after final linking, while actual inputs still exist."""
    policy = _load(POLICY)
    prefix, native_prefix, output = Path(prefix).resolve(), Path(native_prefix).resolve(), Path(output)
    work = prefix.parent / 'work'
    project_root = work / 'mpv-build'
    map_file = Path(map_file)
    text = map_file.read_text(encoding='utf-8')
    records = []
    for spelling in _map_inputs(text):
        path = _native_path(spelling, project_root)
        record = {'mapPath': spelling, 'path': str(path), 'sha256': p.digest(path)}
        if _within(path, prefix) or (_within(path, work) and path.suffix.lower() in ('.o', '.obj')):
            root = prefix if _within(path, prefix) else work
            record.update(owner='project-fixed-source', projectRoot=str(root), relativePath=path.relative_to(root).as_posix())
        else:
            if not _within(path, native_prefix):
                raise ValueError(f'Link input outside fixed project/toolchain roots: {path}')
            owner, version = _owner(path)
            arches = [a for a, entry in policy['architectures'].items() if owner.startswith(entry['packagePrefix'])]
            if len(arches) != 1:
                raise ValueError(f'Unknown link owner: {owner}')
            _package_check(owner, version, policy, arches[0])
            record.update(owner=owner, version=version)
        records.append(record)
    config = output / 'build-config'
    config.mkdir(parents=True, exist_ok=True)
    shutil.copy2(map_file, config / 'mpv-link.map')
    evidence = {'schemaVersion': 1, 'mapSHA256': p.digest(config / 'mpv-link.map'),
                'prefix': str(prefix), 'work': str(work), 'nativePrefix': str(native_prefix), 'inputs': records}
    p.atomic_json(config / 'windows-link-inputs.json', evidence)
    return evidence


def _review_link(output, arch, policy, packages):
    config = output / 'build-config'
    evidence = _load(config / 'windows-link-inputs.json')
    if evidence.get('schemaVersion') != 1 or p.digest(config / 'mpv-link.map') != evidence.get('mapSHA256'):
        raise ValueError('Missing or changed final link map')
    spellings = _map_inputs((config / 'mpv-link.map').read_text(encoding='utf-8'))
    records = evidence.get('inputs', [])
    if len(records) != len(spellings) or sorted(r.get('mapPath', '') for r in records) != spellings:
        raise ValueError('Link map/input inventory mismatch')
    prefix, work, native = (Path(evidence[k]) for k in ('prefix', 'work', 'nativePrefix'))
    if work != prefix.parent / 'work':
        raise ValueError('Invalid project work root')
    owners = set()
    for record in records:
        if not _hash(record.get('sha256')):
            raise ValueError('Missing link input SHA256')
        path = Path(record['path'])
        if not path.is_absolute() or path != _native_path(record['mapPath'], work / 'mpv-build'):
            raise ValueError('Invalid link input path')
        owner = record['owner']
        if owner == 'project-fixed-source':
            root = prefix if _within(path, prefix) else work
            if not _within(path, root) or (root == work and path.suffix.lower() not in ('.o', '.obj')):
                raise ValueError('Invalid project link input')
            if record.get('projectRoot') != str(root) or record.get('relativePath') != path.relative_to(root).as_posix():
                raise ValueError('Invalid relative project input')
        else:
            _package_check(owner, record.get('version'), policy, arch)
            if packages.get(owner) != record['version'] or not _within(path, native):
                raise ValueError('Link owner not in captured toolchain')
            owners.add(owner)
        # Archived evidence remains usable after the native build directory is gone.
        # Recheck any surviving original inputs; on Windows also recheck ownership.
        if path.exists():
            if p.digest(path) != record['sha256']:
                raise ValueError(f'Changed link input: {path}')
            if os.name == 'nt' and owner != 'project-fixed-source' and _owner(path) != (owner, record['version']):
                raise ValueError('Changed native link owner')
    crt = policy['architectures'][arch]['packagePrefix'] + 'crt'
    if crt not in owners:
        raise ValueError('Missing CRT startup/import-library evidence')
    return evidence


def _review_pe(output, arch, policy):
    expected = policy['architectures'][arch]
    runtimes = _load(output / 'runtime-dlls.json')
    if not isinstance(runtimes, list) or len(runtimes) != len(expected['runtimeDLLs']):
        raise ValueError('Unexpected runtime inventory')
    inventory = {r['name']: r['sha256'] for r in runtimes}
    if inventory != expected['runtimeDLLs']:
        raise ValueError('Runtime DLL hash/inventory differs from policy')
    dlls = {file.name.lower(): file for file in output.rglob('*') if file.is_file() and file.suffix.lower() == '.dll'}
    all_dlls = [f for f in output.rglob('*') if f.is_file() and f.suffix.lower() == '.dll']
    if len(dlls) != len(all_dlls) or set(dlls) != {'libmpv-2.dll', *inventory}:
        raise ValueError('Unknown or duplicate DLL')
    dependencies = {}
    for name, file in dlls.items():
        data = file.read_bytes()
        if data[:2] != b'MZ' or len(data) < 64:
            raise ValueError('Invalid PE header')
        offset = struct.unpack_from('<I', data, 60)[0]
        if data[offset:offset + 4] != b'PE\0\0' or struct.unpack_from('<H', data, offset + 4)[0] != expected['machine']:
            raise ValueError(f'Wrong PE architecture: {name}')
        if name in inventory and p.digest(file) != inventory[name]:
            raise ValueError(f'Runtime DLL bytes differ: {name}')
        dependencies[name] = imports(file)
        for dependency in dependencies[name]:
            if not is_system_dll(dependency) and dependency not in dlls:
                raise ValueError(f'Unclosed PE import: {dependency}')
    reachable = {'libmpv-2.dll'}
    pending = ['libmpv-2.dll']
    while pending:
        for name in dependencies[pending.pop()]:
            if name in dlls and name not in reachable:
                reachable.add(name)
                pending.append(name)
    if reachable != set(dlls):
        raise ValueError('Runtime DLL not used by import closure')
    return dependencies


def _source_review(archives, policy):
    """Inspect named recipe/license members only; never unpack source trees."""
    for name, archive in archives.items():
        entry = policy['sources'][name]
        if p.digest(archive) != entry['sha256'] or archive.stat().st_size != entry['size']:
            raise ValueError(f'Source hash/size mismatch: {name}')
        with tarfile.open(archive, 'r:gz') as container:
            root = entry['root']
            first = container.next()
            if first is None or first.name.rstrip('/') != root:
                raise ValueError(f'Wrong source root: {name}')
            paths = {}
            if name == 'msys2-recipes':
                paths.update({f'{folder}/PKGBUILD': None for folder in policy['recipeDirectories']})
                paths.update({f'mingw-w64-libiconv/{path}': sha for path, sha in policy['libiconvRecipeFiles'].items()})
            elif name == 'libiconv':
                paths['COPYING.LIB'] = policy['licenseSHA256']['libiconv/COPYING.LIB']
            else:
                paths['COPYING'] = policy['licenseSHA256']['crt/COPYING']
            for path, expected in paths.items():
                member = container.getmember(f'{root}/{path}')
                if not member.isfile() or member.size > 2 * 1024 * 1024:
                    raise ValueError(f'Invalid source recipe/license: {path}')
                data = container.extractfile(member).read()
                if expected and _sha(data) != expected:
                    raise ValueError(f'Source recipe/license hash mismatch: {path}')


def complete_materials(output, target, cache, offline=False):
    """Validate all prerequisites before writing a completed runtime review."""
    policy = _load(POLICY)
    if target not in ('windows/amd64', 'windows/arm64'):
        raise ValueError('Windows runtime review requires a supported Windows target')
    output, cache = Path(output), Path(cache)
    arch = target.split('/')[1]
    toolchain = _load(output / 'build-config/toolchain.json')
    packages = {}
    for line in toolchain['packages'].splitlines():
        fields = line.split()
        if len(fields) == 2:
            if fields[0] in packages:
                raise ValueError('Duplicate toolchain package')
            packages[fields[0]] = fields[1]
    prefix = policy['architectures'][arch]['packagePrefix']
    for suffix, version in policy['packageVersions'].items():
        if packages.get(prefix + suffix) != version:
            raise ValueError(f'Unexpected toolchain package version: {suffix}')
    for path, expected in policy['licenseSHA256'].items():
        if p.digest(output / 'licenses/windows-toolchain' / path) != expected:
            raise ValueError(f'License hash mismatch: {path}')
    dependencies = _review_pe(output, arch, policy)
    evidence = _review_link(output, arch, policy, packages)
    archives = {name: p.fetch(entry, cache, offline) for name, entry in policy['sources'].items()}
    _source_review(archives, policy)
    destination = output / 'sources/windows'
    destination.mkdir(parents=True, exist_ok=True)
    for name, archive in archives.items():
        copied = destination / archive.name
        shutil.copy2(archive, copied)
        if p.digest(copied) != policy['sources'][name]['sha256']:
            raise ValueError('Copied source hash mismatch')
    scripts = output / 'rebuild/scripts/mpv'
    scripts.mkdir(parents=True, exist_ok=True)
    for source in (POLICY, Path(__file__)):
        shutil.copy2(source, scripts / source.name)
    report = {'schemaVersion': 1, 'target': target,
              'correspondingSourceStatus': policy['correspondingSourceStatus'],
              'policySHA256': p.digest(POLICY), 'scope': policy['scope'],
              'imports': dependencies, 'linkMapSHA256': evidence['mapSHA256'],
              'linkInputCount': len(evidence['inputs']), 'sources': policy['sources'],
              'rebuildInstructions': policy['rebuildInstructions']}
    p.atomic_json(output / 'windows-runtime-review.json', report)
    toolchain['runtimeSourceStatus'] = report['correspondingSourceStatus']
    p.atomic_json(output / 'build-config/toolchain.json', toolchain)
    return report
