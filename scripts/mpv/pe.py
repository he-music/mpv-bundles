"""只读取 PE 导入表，区分真正的 DLL 依赖和二进制中的任意字符串。"""
from pathlib import Path
import struct


SYSTEM_DLLS = frozenset({
    "kernel32.dll", "kernelbase.dll", "ntdll.dll", "ucrtbase.dll", "msvcrt.dll",
    "advapi32.dll", "avrt.dll", "bcrypt.dll", "crypt32.dll", "dwmapi.dll", "dwrite.dll",
    "gdi32.dll", "imm32.dll", "iphlpapi.dll", "ksuser.dll", "mf.dll", "mfplat.dll",
    "mfreadwrite.dll", "mfuuid.dll", "mmdevapi.dll", "msimg32.dll", "ncrypt.dll",
    "normaliz.dll", "ole32.dll", "oleaut32.dll", "powrprof.dll", "propsys.dll",
    "pathcch.dll", "shcore.dll", "uxtheme.dll", "psapi.dll", "rpcrt4.dll", "secur32.dll", "setupapi.dll", "shell32.dll", "shlwapi.dll",
    "user32.dll", "userenv.dll", "version.dll", "winmm.dll", "winspool.drv", "ws2_32.dll",
})


def is_system_dll(name):
    value = name.lower()
    return value in SYSTEM_DLLS or value.startswith(("api-ms-win-", "ext-ms-win-"))


def imports(path):
    data = Path(path).read_bytes()
    if data[:2] != b"MZ" or len(data) < 64:
        raise ValueError("无效 PE 文件")
    pe = struct.unpack_from("<I", data, 60)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("缺少 PE 签名")
    coff = pe + 4
    sections, optional_size = struct.unpack_from("<H", data, coff + 2)[0], struct.unpack_from("<H", data, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in (0x10b, 0x20b):
        raise ValueError("不支持的 PE optional header")
    directories = optional + (96 if magic == 0x10b else 112)
    image_base = struct.unpack_from("<I" if magic == 0x10b else "<Q", data, optional + (28 if magic == 0x10b else 24))[0]
    section_table = optional + optional_size
    ranges = []
    for i in range(sections):
        offset = section_table + 40 * i
        virtual_size, virtual, size, raw = struct.unpack_from("<IIII", data, offset + 8)
        ranges.append((virtual, max(virtual_size, size), raw, size))

    def file_offset(rva):
        for virtual, span, raw, size in ranges:
            if virtual <= rva < virtual + span and rva - virtual < size:
                result = raw + rva - virtual
                if result < len(data):
                    return result
        raise ValueError(f"PE RVA 未映射: {rva:x}")

    def dll(rva):
        start = file_offset(rva)
        end = data.find(b"\0", start, start + 512)
        if end < start:
            raise ValueError("PE DLL 名称未终止")
        name = data[start:end].decode("ascii").lower()
        if not name or "/" in name or "\\" in name or ":" in name:
            raise ValueError("PE DLL 名称无效")
        return name

    result = set()
    for index, stride, name_index in ((1, 20, 3), (13, 32, 1)):
        rva, size = struct.unpack_from("<II", data, directories + index * 8)
        if not rva:
            continue
        offset = file_offset(rva)
        if size < stride:
            raise ValueError("PE 导入表过短")
        for descriptor in range(size // stride):
            values = struct.unpack_from("<" + "I" * (stride // 4), data, offset + descriptor * stride)
            if not any(values):
                break
            address = values[name_index]
            if index == 13 and not values[0] & 1:
                address -= image_base
            result.add(dll(address))
        else:
            raise ValueError("PE 导入表缺少终止项")
    return sorted(result)
