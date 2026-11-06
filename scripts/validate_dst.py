#!/usr/bin/env python3
"""用自动生成的 DST 原始帧验证解码器入口；不替代真实压缩帧回归。"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/mpv"))
import prepare
import smoke


def fixture(path):
    def chunk(tag, value):
        return tag + struct.pack(">Q", len(value)) + value + (b"\0" if len(value) % 2 else b"")

    rate, frames, channels = 2822400, 450, 2
    description = b"DST raw frames"
    properties = (b"SND " + chunk(b"FS  ", struct.pack(">I", rate))
                  + chunk(b"CHNL", struct.pack(">H", channels) + b"SLFTSRGT")
                  + chunk(b"CMPR", b"DST " + bytes([len(description)]) + description))
    # FFmpeg dstdec.c 中首位 0 表示 DST 内的原始 DSD 帧；其余头部位为零。
    packet = b"\0" + b"\x69" * (rate // 75 // 8 * channels)
    dst = chunk(b"FRTE", struct.pack(">IH", frames, 75)) + chunk(b"DSTF", packet) * frames
    body = b"DSD " + chunk(b"FVER", struct.pack(">I", 0x01050000)) + chunk(b"PROP", properties) + chunk(b"DST ", dst)
    path.write_bytes(chunk(b"FRM8", body))
    return path


def validate(resources, target):
    resources = resources.resolve(strict=True)
    previous = prepare.verify(resources, target)
    with tempfile.TemporaryDirectory(prefix="mpv-synthetic-dst-") as temporary:
        sample = fixture(Path(temporary) / "synthetic-dst.dff")
        result = smoke.exercise(resources, sample)
        result["fixture"] = {"kind": "synthetic DST raw frames", "sha256": prepare.digest(sample),
                             "arithmeticCompressedFrames": False, "privateAudio": False}
    result["dstRegression"]["scope"] = "synthetic raw frames inside DST packets; arithmetic compression not exercised"
    prepare.atomic_json(resources / "dst-frame-validation.json", result)
    shutil.copy2(Path(__file__), resources / "rebuild/scripts/validate_dst.py")
    details = {**previous["details"], "syntheticDSTSmoke": "dst-frame-validation.json"}
    prepare.write_manifest(resources, target, details)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--target", choices=sorted(prepare.TARGETS))
    parser.add_argument("--generate", type=Path)
    args = parser.parse_args()
    if args.generate:
        args.generate.parent.mkdir(parents=True, exist_ok=True)
        print(fixture(args.generate))
    else:
        if not args.resources or not args.target:
            parser.error("验证需要 --resources 和 --target")
        print(json.dumps(validate(args.resources, args.target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
