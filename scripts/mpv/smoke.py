#!/usr/bin/env python3
"""用临时合成音频和可选只读 DST 样本验证真实 libmpv；始终使用空音频输出。"""
import argparse
import ctypes
import json
import math
from pathlib import Path
import platform
import struct
import sys
import tempfile
import time
import wave


class NativeEvent(ctypes.Structure):
    _fields_ = [("event_id", ctypes.c_int), ("error", ctypes.c_int),
                ("reply_userdata", ctypes.c_uint64), ("data", ctypes.c_void_p)]


class EndFile(ctypes.Structure):
    _fields_ = [("reason", ctypes.c_int), ("error", ctypes.c_int)]


def samples(directory):
    wav = directory / "synthetic.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(
            struct.pack("<hh", *([int(math.sin(i * math.tau * 440 / 48000) * 1000)] * 2))
            for i in range(48000 * 6)))
    rate, count, block = 2822400, 2822400 * 6, 4096
    payload = b"\x69" * (((count // 8 + block - 1) // block) * block * 2)
    fmt_value = struct.pack("<IIIIIIQII", 1, 0, 2, 2, rate, 1, count, block, 0)
    fmt = b"fmt " + struct.pack("<Q", 12 + len(fmt_value)) + fmt_value
    data = b"data" + struct.pack("<Q", 12 + len(payload)) + payload
    dsf_size = 28 + len(fmt) + len(data)
    (directory / "synthetic.dsf").write_bytes(
        b"DSD " + struct.pack("<QQQ", 28, dsf_size, 0) + fmt + data)

    def chunk(tag, value):
        return tag + struct.pack(">Q", len(value)) + value + (b"\0" if len(value) % 2 else b"")

    properties = (b"SND " + chunk(b"FS  ", struct.pack(">I", rate))
                  + chunk(b"CHNL", struct.pack(">H", 2) + b"SLFTSRGT")
                  + chunk(b"CMPR", b"DSD " + bytes([14]) + b"not compressed"))
    body = (b"DSD " + chunk(b"FVER", struct.pack(">I", 0x01050000))
            + chunk(b"PROP", properties) + chunk(b"DSD ", b"\x69" * (count // 8 * 2)))
    (directory / "synthetic.dff").write_bytes(chunk(b"FRM8", body))
    return [wav, directory / "synthetic.dsf", directory / "synthetic.dff"]


def exercise(resources, dst_sample=None):
    names = {"darwin": "libmpv.2.dylib", "linux": "libmpv.so.2", "win32": "libmpv-2.dll"}
    library = (resources / names[sys.platform]).resolve(strict=True)
    api = ctypes.CDLL(str(library), winmode=0x900) if sys.platform == "win32" else ctypes.CDLL(str(library))
    api.mpv_client_api_version.restype = ctypes.c_ulong
    version = api.mpv_client_api_version()
    if version >> 16 != 2 or (version & 0xffff) < 6:
        raise ValueError(f"unsupported client API: {version}")
    api.mpv_create.restype = ctypes.c_void_p
    api.mpv_initialize.argtypes = [ctypes.c_void_p]
    api.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    api.mpv_set_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    api.mpv_get_property_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    api.mpv_get_property_string.restype = ctypes.c_void_p
    api.mpv_get_property.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    api.mpv_free.argtypes = [ctypes.c_void_p]
    api.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    api.mpv_wait_event.argtypes = [ctypes.c_void_p, ctypes.c_double]
    api.mpv_wait_event.restype = ctypes.POINTER(NativeEvent)
    api.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
    handle = api.mpv_create()
    if not handle:
        raise RuntimeError("mpv_create failed")

    def check(code):
        if code < 0:
            raise RuntimeError(f"libmpv call failed: {code}")

    def option(key, value):
        check(api.mpv_set_option_string(handle, key.encode(), value.encode()))

    def prop(key, value):
        check(api.mpv_set_property_string(handle, key.encode(), value.encode()))

    def text(key):
        value = api.mpv_get_property_string(handle, key.encode())
        if not value:
            return None
        try:
            return ctypes.string_at(value).decode()
        finally:
            api.mpv_free(value)

    def number(key):
        value = ctypes.c_double()
        result = api.mpv_get_property(handle, key.encode(), 5, ctypes.byref(value))
        return value.value if result >= 0 else None

    def command(*args):
        values = (ctypes.c_char_p * (len(args) + 1))(*[arg.encode() for arg in args], None)
        check(api.mpv_command(handle, values))

    def drain():
        loaded = False
        while True:
            event = api.mpv_wait_event(handle, 0).contents
            if event.event_id == 0:
                return loaded
            if event.event_id == 8:
                loaded = True
            if event.event_id == 7 and event.data:
                # 临时 C 指针只在本次 wait_event 数据有效期内读取。
                ended = ctypes.cast(event.data, ctypes.POINTER(EndFile)).contents
                if ended.reason == 4 or ended.error < 0:
                    raise RuntimeError(f"playback failed: {ended.error}")
            if event.error < 0:
                raise RuntimeError(f"libmpv event failed: {event.error}")

    def wait_for(predicate, label, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            drain()
            if predicate():
                return
            time.sleep(0.01)
        raise TimeoutError(f"timed out after {timeout}s waiting for {label}")

    evidence = {"clientAPI": f"{version >> 16}.{version & 0xffff}", "ao": "null",
                "audible": False, "runtimeOS": sys.platform, "runtimeArchitecture": platform.machine(), "samples": [], "dstRegression": {"executed": bool(dst_sample)}}
    try:
        for key, value in {"config": "no", "load-scripts": "no", "osc": "no", "ytdl": "no",
                           "video": "no", "terminal": "no", "ao": "null", "ao-null-buffer": "0.05",
                           "idle": "yes", "keep-open": "yes", "pause": "yes"}.items():
            option(key, value)
        check(api.mpv_initialize(handle))
        evidence.update(mpvVersion=text("mpv-version"), ffmpegVersion=text("ffmpeg-version"))
        with tempfile.TemporaryDirectory(prefix="mpv-bundle-smoke-") as temporary:
            files = [(file, False) for file in samples(Path(temporary))]
            if dst_sample is not None:
                files.append((Path(dst_sample).resolve(strict=True), True))
            for sample, is_dst in files:
                drain()
                prop("pause", "yes")
                command("loadfile", str(sample))
                # 文件载入事件隔离上一首残留属性，不能以 duration 暂可读作为解码成功。
                deadline = time.monotonic() + 15
                while not drain():
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"file-loaded timeout: {sample.suffix}")
                    time.sleep(0.01)
                wait_for(lambda: bool(text("audio-codec-name")), "audio codec")
                codec = text("audio-codec-name")
                if is_dst and codec != "dst":
                    raise RuntimeError(f"explicit DST sample selected codec {codec!r}")
                prop("pause", "no")
                wait_for(lambda: (number("time-pos") or 0) > 0.25, "playback progress")
                prop("pause", "yes")
                time.sleep(0.1)
                paused = number("time-pos")
                time.sleep(0.15)
                drain()
                if paused is None or abs((number("time-pos") or 0) - paused) > 0.1:
                    raise RuntimeError("paused playback clock did not stop")
                command("seek", "1.5", "absolute+exact")
                wait_for(lambda: abs((number("time-pos") or 0) - 1.5) < 0.15, "seek")
                prop("pause", "no")
                wait_for(lambda: (number("time-pos") or 0) > 1.75, "resume after seek")
                item = {"format": sample.suffix, "codec": codec, "timePos": number("time-pos"),
                        "dstRequired": is_dst, "pause": True, "seek": True, "resume": True}
                evidence["samples"].append(item)
                if is_dst:
                    evidence["dstRegression"].update(codec=codec, status="passed")
                command("stop")
                wait_for(lambda: text("idle-active") == "yes", "stop")
    finally:
        api.mpv_terminate_destroy(handle)
    if dst_sample is None:
        evidence["dstRegression"]["status"] = "not-executed: no --dst-sample supplied"
    evidence["destroyed"] = True
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--dst-sample", type=Path, help="只读验证显式提供的 DST 压缩 DFF")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = exercise(args.resources, args.dst_sample)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
