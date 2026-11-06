#!/usr/bin/env python3
"""在完整构建前用真实 Windows 链接快速验证 map 格式与运行库归属。"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/mpv"))
import prepare
from windows_runtime import capture_link_evidence


def main():
    if prepare.host()[0] != "windows":
        raise ValueError("此探针要求原生 Windows MSYS2 工具链")
    native = Path(prepare.run(["cygpath", "-m", os.environ["MINGW_PREFIX"]], capture=True).strip())
    with tempfile.TemporaryDirectory(prefix="mpv-link-probe-") as temporary:
        stage = Path(temporary)
        prefix, build, output = stage / "prefix", stage / "work/mpv-build", stage / "mpv"
        for directory in (prefix, build, output):
            directory.mkdir(parents=True)
        (build / "probe.cpp").write_text(
            '#include <charconv>\n'
            'extern "C" __declspec(dllexport) int probe(char* first, char* last, double value) {\n'
            '  return static_cast<int>(std::to_chars(first, last, value).ptr - first);\n'
            '}\n', encoding="utf-8")
        prepare.run(["clang++", "-std=c++17", "-O2", "-c", "probe.cpp", "-o", "probe.obj"], build)
        map_file = stage / "probe.map"
        prepare.run(["clang++", "-shared", "-static", "probe.obj", "-o", output / "probe.dll",
                     "-Wl,-Map," + map_file.as_posix()], build)
        diagnostic = ROOT / "build/mpv-cache/diagnostics/windows-link-probe.map"
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(map_file, diagnostic)
        evidence = capture_link_evidence(map_file, prefix, native, output)
        owners = sorted({record["owner"] for record in evidence["inputs"]})
        print(json.dumps({"nativeLinkProbe": "passed", "inputs": len(evidence["inputs"]), "owners": owners}))


if __name__ == "__main__":
    main()
