#!/usr/bin/env python3
"""Audit Linux bundle ELF architectures and GLIBC symbol baseline."""
import argparse
import json
from pathlib import Path
import re
import prepare


def audit(root, target, baseline='2.35'):
    system, arch = target.split('/')
    if system != 'linux': raise ValueError('Linux target required')
    records = {}
    limit = tuple(map(int, baseline.split('.')))
    for file in root.rglob('*'):
        if not file.is_file(): continue
        with file.open('rb') as stream:
            if stream.read(4) != b'\x7fELF': continue
        if prepare.binary_arches(file) != {arch}: raise ValueError(f'wrong ELF architecture: {file}')
        text = prepare.run(['readelf', '--version-info', file], capture=True)
        versions = sorted(set(re.findall(r'(?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+', text)))
        required = [tuple(map(int, value.split('_')[1].split('.'))) for value in versions if value.startswith('GLIBC_')]
        if required and max(required) > limit: raise ValueError(f'{file} requires GLIBC_{".".join(map(str, max(required)))}')
        records[file.relative_to(root).as_posix()] = versions
    if not records: raise ValueError('no ELF files in Linux bundle')
    return {'target': target, 'libmpvBuildBaseline': 'Ubuntu 22.04 / glibc 2.35', 'elfSymbolVersions': records, 'desktopCompatibilityVerified': False}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, required=True); parser.add_argument('--target', required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); report = audit(args.root, args.target); prepare.atomic_json(args.output, report); print(json.dumps(report, indent=2))
if __name__ == '__main__': main()
