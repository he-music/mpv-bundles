#!/usr/bin/env python3
"""Prepare pinned native libmpv bundles with atomic integrity checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path(__file__).with_name('sources.lock.json')
TARGETS = {'darwin/universal', 'windows/amd64', 'windows/arm64', 'linux/amd64', 'linux/arm64'}
LIBRARIES = {'darwin': 'libmpv.2.dylib', 'linux': 'libmpv.so.2', 'windows': 'libmpv-2.dll'}
SYSTEM_DLLS = set('kernel32 user32 advapi32 bcrypt msvcrt ntdll ole32 shell32 ws2_32 ucrtbase winmm winhttp'.split())


def digest(file):
    value = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def host():
    system = 'windows' if sys.platform == 'win32' else sys.platform
    machine = platform.machine().lower()
    if machine not in ('arm64', 'aarch64', 'amd64', 'x86_64'): raise ValueError(f'Unsupported architecture: {machine}')
    return system, 'arm64' if machine in ('arm64', 'aarch64') else 'amd64'


def command_args(args):
    values = [value.as_posix() if isinstance(value, Path) else str(value) for value in args]
    if sys.platform == 'win32':
        values = [value.replace('\\', '/') for value in values]
        if Path(values[0]).name == 'configure':
            return ['bash', subprocess.check_output(['cygpath', '-u', values[0]], text=True).strip(), *values[1:]]
    return values


def run(args, cwd=None, env=None, capture=False):
    result = subprocess.run(command_args(args), cwd=cwd, env=env, text=True, stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None, check=False)
    if result.returncode: raise RuntimeError(f'Command failed ({result.returncode}): {args}\n{result.stdout or ""}')
    return result.stdout or ''


def atomic_json(file, value):
    file = Path(file); file.parent.mkdir(parents=True, exist_ok=True); temporary = file.with_suffix(file.suffix + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'); temporary.replace(file)


def fetch(entry, cache, offline=False):
    url = urllib.parse.urlparse(entry['url'])
    if url.scheme != 'https' or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']): raise ValueError('Input requires HTTPS and SHA256')
    name = entry.get('filename', Path(url.path).name)
    if not name or Path(name).name != name: raise ValueError('Invalid source filename')
    cache.mkdir(parents=True, exist_ok=True); file = cache / name
    if file.exists() or file.is_symlink():
        if file.is_symlink() or not file.is_file() or digest(file) != entry['sha256']: raise ValueError(f'Cache digest mismatch: {file}')
        return file
    if offline: raise ValueError(f'Offline cache missing: {file}')
    descriptor, temporary_name = tempfile.mkstemp(prefix=name + '-', suffix='.partial', dir=cache); os.close(descriptor); temporary = Path(temporary_name)
    try:
        with urllib.request.urlopen(urllib.request.Request(entry['url'], headers={'User-Agent': 'he-music-mpv-bundles/1'}), timeout=120) as response, temporary.open('wb') as output:
            if urllib.parse.urlparse(response.url).scheme != 'https': raise ValueError('Non-HTTPS redirect')
            shutil.copyfileobj(response, output)
        if digest(temporary) != entry['sha256']: raise ValueError('Downloaded digest mismatch')
        temporary.replace(file)
    finally: temporary.unlink(missing_ok=True)
    return file


def extract(archive, destination, expected_root):
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()): raise ValueError('Extraction requires an empty directory')
    if zipfile.is_zipfile(archive):
        container = zipfile.ZipFile(archive); members = [(i.filename, i.is_dir(), i.file_size, i) for i in container.infolist()]; unsafe = lambda i: (i.external_attr >> 16) & 0o170000 not in (0, 0o100000, 0o040000); opener = container.open
    else:
        container = tarfile.open(archive); members = [(i.name, i.isdir(), i.size, i) for i in container.getmembers()]; unsafe = lambda i: not (i.isfile() or i.isdir()); opener = container.extractfile
    with container:
        seen, total = set(), 0
        for name, directory, size, item in members:
            relative = PurePosixPath(name)
            if not name or '\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != expected_root: raise ValueError(f'Unsafe archive path: {name}')
            if str(relative).casefold() in seen or unsafe(item): raise ValueError(f'Archive link, duplicate or special file: {name}')
            seen.add(str(relative).casefold()); total += size
            if total > 2 * 1024**3: raise ValueError('Archive exceeds size limit')
        for name, directory, size, item in members:
            target = destination.joinpath(*PurePosixPath(name).parts)
            if directory: target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with opener(item) as source, target.open('xb') as output: shutil.copyfileobj(source, output)
                target.chmod(0o755 if ((item.external_attr >> 16) if isinstance(item, zipfile.ZipInfo) else item.mode) & 0o111 else 0o644)
                if isinstance(item, tarfile.TarInfo):
                    os.utime(target, (item.mtime, item.mtime))
    return destination / expected_root


def binary_arches(file):
    data = Path(file).read_bytes(); machines = {0x8664: 'amd64', 0xAA64: 'arm64', 62: 'amd64', 183: 'arm64', 0x1000007: 'amd64', 0x100000C: 'arm64'}
    try:
        if data[:2] == b'MZ': machine = struct.unpack_from('<H', data, struct.unpack_from('<I', data, 0x3C)[0] + 4)[0]
        elif data[:6] == b'\x7fELF\x02\x01': machine = struct.unpack_from('<H', data, 18)[0]
        elif data[:4] == b'\xcf\xfa\xed\xfe': machine = struct.unpack_from('<I', data, 4)[0]
        elif data[:4] == b'\xca\xfe\xba\xbe':
            count = struct.unpack_from('>I', data, 4)[0]
            if count != 2: raise ValueError('Universal binary must contain two slices')
            return {machines[struct.unpack_from('>I', data, 8 + i * 20)[0]] for i in range(count)}
        else: raise ValueError('Unsupported native binary')
        return {machines[machine]}
    except (IndexError, KeyError, struct.error) as error: raise ValueError('Damaged binary header') from error


def native_libraries(directory): return sorted(f for f in Path(directory).iterdir() if f.suffix in ('.dll', '.dylib') or '.so' in f.name)

def validate_dependencies(directory, system):
    result = {}
    for file in native_libraries(directory):
        if system == 'windows':
            from pe import imports, is_system_dll
            deps = imports(file)
            names = {f.name.lower() for f in native_libraries(directory)}
            unknown = [name for name in deps if name not in names and not is_system_dll(name)]
        elif system == 'darwin':
            deps = re.findall(r'^\s+(\S+) \(', run(['otool', '-L', file], capture=True), re.M)
            names = {value.name for value in native_libraries(directory)}
            unknown = [name for name in deps if not name.startswith(('/usr/lib/', '/System/Library/'))
                       and not (name.startswith('@loader_path/') and name == '@loader_path/' + Path(name).name
                                and Path(name).name in names)]
        else:
            dynamic = run(['readelf', '-d', file], capture=True)
            search_paths = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic)
            if any(part not in ('', '$ORIGIN') for value in search_paths for part in value.split(':')):
                raise ValueError(f'{file.name}: non-portable ELF runtime search path')
            deps = re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic)
            allowed = {'libc.so.6', 'libm.so.6', 'libdl.so.2', 'libpthread.so.0', 'librt.so.1',
                       'libstdc++.so.6', 'libgcc_s.so.1', 'libpulse.so.0', 'ld-linux-x86-64.so.2', 'ld-linux-aarch64.so.1'}
            unknown = [name for name in deps if name not in allowed]
        if unknown: raise ValueError(f'{file.name}: undeclared dependencies {unknown}')
        result[file.name] = deps
    if not result: raise ValueError('No native library')
    return result


def recipe_digest():
    value = hashlib.sha256()
    for file in (LOCK, Path(__file__), Path(__file__).with_name('smoke.py'), Path(__file__).with_name('build.py'), Path(__file__).with_name('audit_linux.py'), Path(__file__).with_name('pe.py'), ROOT / 'requirements-build.txt'):
        value.update(file.read_bytes())
    return value.hexdigest()


def inventory(directory):
    result = {}
    for file in sorted(Path(directory).rglob('*')):
        if file.is_symlink() or not (file.is_file() or file.is_dir()): raise ValueError(f'Invalid bundle entry: {file}')
        if file.is_file() and file != Path(directory) / 'manifest.json': result[file.relative_to(directory).as_posix()] = {'sha256': digest(file), 'size': file.stat().st_size}
    return result


def verify(directory, target, inspect=True):
    manifest = json.loads((Path(directory) / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('recipeSHA256') != recipe_digest() or manifest.get('target') != target: raise ValueError('Bundle target or recipe mismatch')
    if manifest.get('files') != inventory(directory): raise ValueError('Bundle inventory mismatch')
    system, arch = target.split('/'); required = {'amd64', 'arm64'} if arch == 'universal' else {arch}
    if not (Path(directory) / LIBRARIES[system]).is_file():
        raise ValueError('Missing primary libmpv library')
    if any(binary_arches(file) != required for file in native_libraries(directory)):
        raise ValueError('Library architecture mismatch')
    if inspect and validate_dependencies(directory, system) != manifest['dependencies']: raise ValueError('Dynamic dependencies changed')
    return manifest


def write_manifest(directory, target, details):
    lock = json.loads(LOCK.read_text())
    atomic_json(Path(directory) / 'manifest.json', {'schemaVersion': 1, 'mpvVersion': lock['mpvVersion'], 'clientAPI': lock['clientAPI'], 'ffmpegVersion': lock['sources']['ffmpeg']['version'], 'target': target, 'recipeSHA256': recipe_digest(), 'dependencies': validate_dependencies(directory, target.split('/')[0]), 'details': details, 'files': inventory(directory)})
    return verify(directory, target)


def replace_tree(source, destination):
    backup = Path(destination).with_name(Path(destination).name + '.previous')
    if Path(destination).is_symlink() or backup.exists() or backup.is_symlink(): raise ValueError('Unsafe replacement state')
    if Path(destination).exists(): Path(destination).rename(backup)
    try: shutil.move(source, destination)
    except BaseException:
        if backup.exists(): backup.rename(destination)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def prepare(target, cache, offline=False, jobs=4, force=False):
    from build import source_build
    destination = Path(cache) / 'prepared' / target.replace('/', '-') / 'mpv'
    if destination.exists() and not force: verify(destination, target); return destination
    destination.parent.mkdir(parents=True, exist_ok=True); started = recipe_digest()
    with tempfile.TemporaryDirectory(prefix='mpv-build-', dir=cache) as temporary:
        output, details = source_build(target, Path(temporary), cache, json.loads(LOCK.read_text()), offline, jobs)
        if recipe_digest() != started: raise ValueError('Recipe changed during build')
        system, arch = host()
        if target == f'{system}/{arch}': run([sys.executable, Path(__file__).with_name('smoke.py'), '--resources', output, '--output', output / 'validation.json']); details['runtimeSmoke'] = 'validation.json'
        else: details['runtimeSmoke'] = 'not-run: cross-compiled target'
        write_manifest(output, target, details); replace_tree(output, destination)
    return destination


def universal(cache, offline=False, jobs=4, force=False):
    if sys.platform != 'darwin': raise ValueError('Universal build requires macOS')
    halves = [prepare('darwin/' + arch, cache, offline, jobs, force) for arch in ('arm64', 'amd64')]; destination = Path(cache) / 'prepared/darwin-universal/mpv'
    if destination.exists() and not force: verify(destination, 'darwin/universal'); return destination
    with tempfile.TemporaryDirectory(prefix='mpv-universal-', dir=cache) as temporary:
        output = Path(temporary) / 'mpv'; shutil.copytree(halves[0], output); (output / 'manifest.json').unlink(); (output / 'validation.json').unlink(missing_ok=True)
        run(['lipo', '-create', *[h / 'libmpv.2.dylib' for h in halves], '-output', output / 'libmpv.2.dylib']); run(['codesign', '--force', '--sign', '-', output / 'libmpv.2.dylib']); run([sys.executable, Path(__file__).with_name('smoke.py'), '--resources', output, '--output', output / 'validation.json'])
        shutil.rmtree(output / 'build-config')
        for half in halves:
            half_manifest = json.loads((half / 'manifest.json').read_text())
            arch = half_manifest['target'].split('/')[1]
            folder = output / 'build-config' / arch
            shutil.copytree(half / 'build-config', folder)
            shutil.copy2(half / 'build-info.json', folder / 'build-info.json')
            shutil.copy2(half / 'manifest.json', folder / 'slice-manifest.json')
        details = {'kind': 'universal', 'runtimeSmoke': 'validation.json',
                   'runtimeArchitecture': host()[1], 'correspondingSourceStatus': 'project-fixed-sources',
                   'slices': ['arm64', 'amd64']}
        atomic_json(output / 'build-info.json', {**details, 'target': 'darwin/universal',
                    'commit': os.environ.get('GITHUB_SHA', 'local'), 'dstDecoder': True})
        write_manifest(output, 'darwin/universal', details)
        destination.parent.mkdir(parents=True, exist_ok=True)
        replace_tree(output, destination)
    return destination


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--target', choices=sorted(TARGETS), default='darwin/universal' if sys.platform == 'darwin' else '/'.join(host())); parser.add_argument('--cache', type=Path, default=ROOT / 'build/mpv-cache'); parser.add_argument('--offline', action='store_true', default=os.environ.get('MPV_OFFLINE') == '1'); parser.add_argument('--fetch-only', action='store_true'); parser.add_argument('--check', action='store_true'); parser.add_argument('--force', action='store_true'); parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8)); args = parser.parse_args(); args.cache.mkdir(parents=True, exist_ok=True)
    if args.fetch_only:
        for entry in json.loads(LOCK.read_text())['sources'].values(): fetch(entry, args.cache, args.offline)
    elif args.check: verify(args.cache / 'prepared' / args.target.replace('/', '-') / 'mpv', args.target)
    else: print(universal(args.cache, args.offline, args.jobs, args.force) if args.target == 'darwin/universal' else prepare(args.target, args.cache, args.offline, args.jobs, args.force))

if __name__ == '__main__':
    try: main()
    except (ValueError, RuntimeError, OSError, tarfile.TarError, zipfile.BadZipFile, KeyError, struct.error) as error: print(f'libmpv preparation failed: {error}', file=sys.stderr); sys.exit(1)
