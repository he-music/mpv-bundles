"""Native audio-only source recipe for pinned libmpv bundles."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

import prepare as p

# FFmpeg names compressed DSDIFF audio decoder dst; keep this explicit regression contract.
AUDIO_DECODERS = ['aac', 'aac_fixed', 'aac_latm', 'ac3', 'alac', 'als', 'amrnb', 'amrwb', 'ape', 'atrac1', 'atrac3', 'atrac3al', 'atrac3p', 'atrac3pal', 'cook', 'dca', 'dolby_e', 'dsd_lsbf', 'dsd_lsbf_planar', 'dsd_msbf', 'dsd_msbf_planar', 'dst', 'eac3', 'flac', 'g723_1', 'g729', 'gsm', 'gsm_ms', 'iac', 'imc', 'mace3', 'mace6', 'mlp', 'mp1', 'mp1float', 'mp2', 'mp2float', 'mp3', 'mp3float', 'mp3adu', 'mp3adufloat', 'mp3on4', 'mp3on4float', 'mpc7', 'mpc8', 'nellymoser', 'opus', 'pcm*', 'adpcm*', 'qcelp', 'qdm2', 'qdmc', 'ralf', 'real_144', 'real_288', 's302m', 'shorten', 'sipr', 'speex', 'tak', 'truehd', 'truespeech', 'tta', 'twinvq', 'vorbis', 'wavpack', 'wmalossless', 'wmapro', 'wmav1', 'wmav2', 'wmavoice']
AUDIO_DEMUXERS = 'ape,dsf,iff,flac,wav,aiff,mp3,aac,ogg,mov,matroska,asf,wv,tta,tak,mpc,mpc8,amr,ac3,eac3,dts'


def check_native(target, lock):
    system, arch = target.split('/')
    host_system, host_arch = p.host()
    if system != host_system or (system in ('linux', 'windows') and arch != host_arch):
        raise ValueError('Linux/Windows builds require matching native architecture')
    if system == 'windows':
        expected = 'CLANGARM64' if arch == 'arm64' else 'CLANG64'
        if os.environ.get('MSYSTEM') != expected: raise ValueError(f'Windows target requires MSYS2 {expected}')
    if system == 'linux':
        actual = platform.libc_ver()[1]
        if not actual or tuple(map(int, actual.split('.'))) > tuple(map(int, lock['linuxGlibcBaseline'].split('.'))):
            raise ValueError('Build Linux in locked Ubuntu 22.04 / glibc 2.35 container')


def apply_coreaudio_fix(source):
    file = source / 'meson.build'
    before = "if features['coreaudio']\n    dependencies += coreaudio\n"
    after = before + "    sources += files('osdep/utils-mac.c')\n"
    text = file.read_text(encoding='utf-8')
    if text.count(before) != 1 or after in text: raise ValueError('CoreAudio patch context mismatch')
    file.write_text(text.replace(before, after), encoding='utf-8')
    return {'file': 'meson.build', 'resultSHA256': p.digest(file)}


def ffmpeg_options(prefix, cflags, ldflags):
    return [f'--prefix={prefix}', f'--libdir={prefix / "lib"}', '--enable-static', '--disable-shared', '--enable-pic', '--disable-autodetect', '--disable-gpl', '--disable-nonfree', '--disable-programs', '--disable-doc', '--disable-debug', '--enable-network', '--disable-avdevice', '--disable-encoders', '--disable-muxers', '--disable-hwaccels', '--disable-videotoolbox', '--disable-audiotoolbox', '--disable-x86asm', '--disable-v4l2-m2m', '--disable-vaapi', '--disable-vdpau', '--disable-vulkan', '--disable-decoders', '--enable-decoder=' + ','.join(AUDIO_DECODERS), '--disable-demuxers', '--enable-demuxer=' + AUDIO_DEMUXERS, '--disable-protocols', '--enable-protocol=file,http,tcp,pipe,crypto', '--disable-filters', '--enable-filter=aresample,aformat,anull,atempo,volume,pan,abuffer,abuffersink', '--extra-cflags=' + cflags, '--extra-ldflags=' + ldflags]


def materials(output, sources, archives, lock):
    for name, source in sources.items():
        folder = output / 'licenses' / name; folder.mkdir(parents=True)
        for relative in lock['sources'][name]['licenses']: shutil.copy2(source / relative, folder / Path(relative).name)
    source_dir = output / 'sources'; source_dir.mkdir()
    for archive in archives.values(): shutil.copy2(archive, source_dir / archive.name)
    shutil.copy2(p.LOCK, output / 'sources.lock.json')
    scripts = output / 'rebuild/scripts/mpv'; scripts.mkdir(parents=True)
    for name in ('prepare.py', 'build.py', 'smoke.py', 'audit_linux.py', 'pe.py', 'sources.lock.json'): shutil.copy2(Path(__file__).with_name(name), scripts / name)
    shutil.copy2(p.ROOT / 'requirements-build.txt', output / 'rebuild/requirements-build.txt')
    shutil.copy2(p.ROOT / 'LICENSES.md', output / 'licenses/NOTICES.md')
    if p.host()[0] == 'windows':
        mingw = os.environ.get('MINGW_PREFIX')
        if not mingw:
            raise ValueError('Missing MSYS2 MINGW_PREFIX')
        native_prefix = Path(p.run(['cygpath', '-m', mingw], capture=True).strip())
        notices = native_prefix / 'share/licenses'
        if not notices.is_dir() or not any(notices.rglob('*')):
            raise ValueError('Missing Windows toolchain license materials')
        shutil.copytree(notices, output / 'licenses/windows-toolchain')


def source_build(target, stage, cache, lock, offline, jobs):
    check_native(target, lock)
    system, arch = target.split('/')
    tools = ['meson', 'ninja', 'make'] + (['clang', 'clang++', 'llvm-ar', 'llvm-strip', 'bash', 'cygpath'] if system == 'windows' else ['cc', 'c++'])
    for tool in tools:
        if not shutil.which(tool): raise ValueError(f'Missing build tool: {tool}')
    archives = {name: p.fetch(entry, cache, offline) for name, entry in lock['sources'].items()}
    work = stage / 'work'; sources = {name: p.extract(file, work / name, lock['sources'][name]['root']) for name, file in archives.items()}
    prefix = (stage / 'prefix').resolve(); prefix.mkdir()
    env = dict(os.environ, PKG_CONFIG_PATH='', PKG_CONFIG_LIBDIR=(prefix / 'lib/pkgconfig').as_posix(), CFLAGS='-O2 -fPIC', CXXFLAGS='-O2 -fPIC', LDFLAGS='', SOURCE_DATE_EPOCH='1793836800')
    cross_options, ffmpeg_cross = [], []
    if system == 'darwin':
        apple_arch = 'arm64' if arch == 'arm64' else 'x86_64'; env.update(CC=f'clang -arch {apple_arch}', CXX=f'clang++ -arch {apple_arch}', MACOSX_DEPLOYMENT_TARGET='11.0')
        env['CFLAGS'] += f' -arch {apple_arch} -mmacosx-version-min=11.0'; env['CXXFLAGS'] += f' -arch {apple_arch} -mmacosx-version-min=11.0'; env['LDFLAGS'] = f'-arch {apple_arch} -mmacosx-version-min=11.0'
    elif system == 'linux':
        triplet = 'aarch64-linux-gnu' if arch == 'arm64' else 'x86_64-linux-gnu'; env['PKG_CONFIG_LIBDIR'] += f':/usr/lib/{triplet}/pkgconfig:/usr/share/pkgconfig'
    else:
        env.update(CC='clang', CXX='clang++', AR='llvm-ar', RANLIB='llvm-ranlib', CFLAGS='-O2', CXXFLAGS='-O2', LDFLAGS='-static -static-libgcc -static-libstdc++'); env['SHELL'] = '/usr/bin/bash'
    env['LDFLAGS'] += f' -L"{(prefix / "lib").as_posix()}"'
    commands = []
    def execute(args, cwd=None, custom_env=None):
        commands.append(p.command_args(args)); return p.run(args, cwd, custom_env or env)
    tool_env = {**env, 'CC': 'clang' if system == 'windows' else 'cc', 'CXX': 'clang++' if system == 'windows' else 'c++', 'CFLAGS': '-O2', 'CXXFLAGS': '-O2', 'LDFLAGS': '', 'CPPFLAGS': '-DPKGCONFIG_IS_STATIC' if system == 'windows' else ''}
    execute([sources['pkgconf'] / 'configure', f'--prefix={prefix}', '--disable-shared'], sources['pkgconf'], tool_env); execute(['make', f'-j{jobs}'], sources['pkgconf'], tool_env); execute(['make', 'install'], sources['pkgconf'], tool_env)
    if system == 'windows':
        pkgconf = prefix / 'bin/pkgconf.exe'; env['PKG_CONFIG'] = pkgconf.as_posix(); native = stage / 'windows-native.ini'; native.write_text("[binaries]\nc = 'clang'\ncpp = 'clang++'\nar = 'llvm-ar'\nstrip = 'llvm-strip'\npkg-config = ['" + pkgconf.as_posix() + "', '--static']\n", encoding='utf-8'); cross_options = ['--native-file', native]; ffmpeg_cross = ['--target-os=mingw32', '--arch=' + ('aarch64' if arch == 'arm64' else 'x86_64'), '--cc=clang', '--cxx=clang++', '--ar=llvm-ar', '--ranlib=llvm-ranlib', '--nm=llvm-nm', '--pkg-config=' + env['PKG_CONFIG'], '--pkg-config-flags=--static']
    else:
        wrapper = prefix / 'bin/mpv-pkg-config'; wrapper.write_text('#!/bin/sh\nexec "' + str(prefix / 'bin/pkgconf') + '" --static "$@"\n', encoding='utf-8'); wrapper.chmod(0o755); env['PKG_CONFIG'] = str(wrapper)
        if system == 'darwin' and arch != p.host()[1]:
            cross = stage / 'cross.ini'; cross.write_text("[binaries]\nc = ['clang', '-arch', '" + apple_arch + "']\ncpp = ['clang++', '-arch', '" + apple_arch + "']\nobjc = ['clang', '-arch', '" + apple_arch + "']\nobjcpp = ['clang++', '-arch', '" + apple_arch + "']\nar = 'ar'\nstrip = 'strip'\npkg-config = '" + str(wrapper) + "'\n[host_machine]\nsystem = 'darwin'\ncpu_family = '" + apple_arch + "'\ncpu = '" + apple_arch + "'\nendian = 'little'\n[properties]\nneeds_exe_wrapper = true\n", encoding='utf-8'); cross_options = ['--cross-file', cross]; ffmpeg_cross = ['--enable-cross-compile', '--target-os=darwin', '--arch=' + apple_arch]
    def meson(name, options):
        build = work / (name + '-build'); execute(['meson', 'setup', build, sources[name], f'--prefix={prefix}', '--libdir=lib', '--buildtype=release', '--default-library=static', '--wrap-mode=nodownload', '-Db_staticpic=true', '-Db_lundef=true', '-Dauto_features=disabled', *cross_options, *options]); execute(['meson', 'compile', '-C', build, f'-j{jobs}']); execute(['meson', 'install', '-C', build])
    # 保留 Lua 选项以兼容 HE-Music；实例以 load-scripts=no 关闭自动加载。
    lua = sources['lua']
    execute(['make', '-C', lua / 'src', 'liblua.a', 'CC=' + env.get('CC', 'cc'),
             'MYCFLAGS=' + env['CFLAGS'] + (' -DLUA_USE_WINDOWS' if system == 'windows' else ''),
             'AR=' + ('llvm-ar rcu' if system == 'windows' else 'ar rcu'),
             'RANLIB=' + env.get('RANLIB', 'ranlib')], lua)
    shutil.copy2(lua / 'src/liblua.a', prefix / 'lib/liblua.a')
    shutil.copytree(lua / 'src', prefix / 'include/lua5.2',
                    ignore=shutil.ignore_patterns('*.c', '*.o', '*.a', 'Makefile'))
    (prefix / 'lib/pkgconfig/lua52.pc').write_text(
        f'prefix={prefix.as_posix()}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include/lua5.2\n'
        'Name: Lua\nDescription: Lua 5.2\nVersion: 5.2.4\nLibs: -L${libdir} -llua'
        + ('' if system == 'windows' else ' -lm') + '\nCflags: -I${includedir}\n', encoding='utf-8')
    meson('freetype', ['-Dharfbuzz=disabled', '-Dzlib=disabled']); meson('fribidi', ['-Ddocs=false', '-Dbin=false', '-Dtests=false']); meson('harfbuzz', ['-Dfreetype=enabled', '-Dtests=disabled', '-Dutilities=disabled']); meson('libass', ['-Drequire-system-font-provider=false'])
    shutil.copytree(sources['fast_float'] / 'include/fast_float', sources['libplacebo'] / '3rdparty/fast_float/include/fast_float', dirs_exist_ok=True); shutil.copytree(sources['vulkan-headers'] / 'include', prefix / 'include', dirs_exist_ok=True); env['CFLAGS'] += f' -I"{(prefix / "include").as_posix()}"'; env['CXXFLAGS'] += f' -I"{(prefix / "include").as_posix()}"'; meson('libplacebo', ['-Ddemos=false', '-Dtests=false', '-Dbench=false', '-Dfuzz=false'])
    ffmpeg = sources['ffmpeg']; execute([ffmpeg / 'configure', *ffmpeg_options(prefix, env['CFLAGS'], env['LDFLAGS']), *ffmpeg_cross], ffmpeg)
    config = (ffmpeg / 'config.h').read_text(encoding='utf-8'); components = (ffmpeg / 'config_components.h').read_text(encoding='utf-8') if (ffmpeg / 'config_components.h').exists() else ''
    if not re.search(r'^#define CONFIG_DST_DECODER 1$', config, re.M) and not re.search(r'^#define CONFIG_DST_DECODER 1$', components, re.M): raise ValueError('FFmpeg DST decoder is not enabled')
    execute(['make', f'-j{jobs}'], ffmpeg); execute(['make', 'install'], ffmpeg)
    if system != 'windows': env['LDFLAGS'] += ' -lc++' if system == 'darwin' else ' -lstdc++'
    adjustments = [apply_coreaudio_fix(sources['mpv'])] if system == 'darwin' else []
    mpv_options = ['--default-library=shared', '-Dgpl=false', '-Dcplayer=false', '-Dlibmpv=true', '-Dbuild-date=false', '-Dlua=lua52', '-Dgl=disabled', '-Dmanpage-build=disabled'] + (['-Dcoreaudio=enabled'] if system == 'darwin' else ['-Dwasapi=enabled', '-Dwin32-threads=enabled'] if system == 'windows' else ['-Dpulse=enabled'])
    meson('mpv', mpv_options)
    output = stage / 'mpv'; output.mkdir(); filename = p.LIBRARIES[system]; shutil.copy2((prefix / ('bin' if system == 'windows' else 'lib') / filename).resolve(), output / filename)
    if system == 'darwin': execute(['install_name_tool', '-id', '@loader_path/libmpv.2.dylib', output / filename]); execute(['codesign', '--force', '--sign', '-', output / filename]); execute(['codesign', '--verify', '--strict', output / filename])
    else: execute(['llvm-strip' if system == 'windows' else 'strip', '--strip-unneeded', output / filename])
    runtime_dlls = []
    if system == 'windows':
        from pe import imports, is_system_dll
        pending = [output / filename]
        search = [prefix / 'bin', *[Path(value) for value in os.environ.get('PATH', '').split(os.pathsep) if value]]
        while pending:
            for dependency in imports(pending.pop()):
                if is_system_dll(dependency) or (output / dependency).exists():
                    continue
                matches = [folder / dependency for folder in search if (folder / dependency).is_file()]
                if not matches:
                    raise ValueError(f'Missing compiler runtime DLL: {dependency}')
                source = matches[0]
                shutil.copy2(source, output / dependency)
                runtime_dlls.append({'name': dependency, 'source': str(source), 'sha256': p.digest(source)})
                pending.append(output / dependency)
        p.atomic_json(output / 'runtime-dlls.json', runtime_dlls)
    materials(output, sources, archives, lock); config_dir = output / 'build-config'; config_dir.mkdir()
    for name in ('freetype', 'fribidi', 'harfbuzz', 'libass', 'libplacebo', 'mpv'):
        folder = config_dir / name; folder.mkdir(); shutil.copy2(work / (name + '-build') / 'meson-info/intro-buildoptions.json', folder / 'options.json'); shutil.copy2(work / (name + '-build') / 'meson-logs/meson-log.txt', folder / 'meson-log.txt')
    toolchain = {'compiler': p.run(['clang' if system == 'windows' else 'cc', '--version'], capture=True), 'python': sys.version}
    if system == 'windows': toolchain.update(msystem=os.environ['MSYSTEM'], runnerImage=os.environ.get('ImageVersion', 'unknown'), packages=p.run(['pacman', '-Q'], capture=True), packageMetadata=p.run(['pacman', '-Qi'], capture=True), runtimeSourceStatus='pending-toolchain-runtime-review'); shutil.copy2(native, config_dir / 'windows-native.ini')
    elif system == 'darwin': toolchain['sdk'] = p.run(['xcrun', '--show-sdk-version'], capture=True)
    else: toolchain['systemPackages'] = p.run(['dpkg-query', '-W', '-f=${Package}=${Version}\\n', 'libc6', 'libpulse0', 'libstdc++6'], capture=True)
    p.atomic_json(config_dir / 'toolchain.json', toolchain); shutil.copy2(ffmpeg / 'config.h', config_dir / 'ffmpeg-config.h'); shutil.copy2(ffmpeg / 'config_components.h', config_dir / 'ffmpeg-config_components.h'); shutil.copy2(ffmpeg / 'ffbuild/config.mak', config_dir / 'ffmpeg-config.mak'); p.atomic_json(config_dir / 'commands.json', commands); p.atomic_json(config_dir / 'mpv-source-adjustments.json', adjustments)
    p.atomic_json(output / 'build-info.json', {'target': target, 'host': platform.platform(), 'commit': os.environ.get('GITHUB_SHA', 'local'), 'repository': os.environ.get('GITHUB_REPOSITORY', 'he-music/mpv-bundles'), 'runID': os.environ.get('GITHUB_RUN_ID', 'local'), 'audioOnly': True, 'scripts': 'supported; automatic loading disabled by consumers', 'dstDecoder': True})
    if system == 'linux':
        from audit_linux import audit
        p.atomic_json(output / 'linux-audit.json', audit(output, target, lock['linuxGlibcBaseline']))
    return output, {'kind': 'source', 'audioOnly': True, 'scripts': 'supported; automatic loading disabled by consumers', 'dstDecoder': True, 'runtimeArchitecture': arch, 'correspondingSourceStatus': 'pending-toolchain-runtime-review' if system == 'windows' else 'project-fixed-sources', 'linuxBuildImage': lock['linuxBuildImage'] if system == 'linux' else None}
