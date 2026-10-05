"""Readable UTF-8 translation exchange (version 1).

Blocks contain 원문: and 번역: sections. Every content line is preserved;
backslashes use \\\\, carriage returns use \\r, and reserved delimiter lines
are prefixed with a backslash. The newline before a delimiter is structural.
"""
from __future__ import annotations

import ctypes
from pathlib import Path

from .editor_store import validate_translation

HEADER = "# RW3KoreanSanzuRiverr 번역 교환 v1"
DELIMITERS = {"[번역]", "원문:", "번역:", "[/번역]"}
MAX_BYTES = 5 * 1024 * 1024


def _encode(value):
    lines = value.replace("\\", "\\\\").replace("\r", "\\r").split("\n")
    return "\n".join("\\" + line if line in DELIMITERS else line for line in lines)


def _decode(lines):
    result = []
    for line in lines:
        if line.startswith("\\") and line[1:] in DELIMITERS:
            result.append(line[1:])
            continue
        output = []
        index = 0
        while index < len(line):
            char = line[index]
            if char == "\\":
                index += 1
                if index == len(line) or line[index] not in ("\\", "r"):
                    raise ValueError("잘못된 역슬래시 이스케이프입니다.")
                char = "\r" if line[index] == "r" else "\\"
            output.append(char)
            index += 1
        result.append("".join(output))
    return "\n".join(result)


def export_text(path, overrides):
    """Write a new file exclusively; never overwrite an existing export."""
    blocks = [HEADER]
    for en, ko in sorted(overrides.items()):
        validate_translation(en, ko)
        blocks.append("[번역]\n원문:\n" + _encode(en) + "\n번역:\n" + _encode(ko) + "\n[/번역]")
    data = ("\n\n".join(blocks) + "\n").encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("번역 교환 파일은 5 MB 이하여야 합니다.")
    with Path(path).open("xb") as stream:
        stream.write(data)
    return Path(path)


def read_text(path):
    """Return valid entries and visible errors; callers must block errors."""
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("번역 교환 파일은 5 MB 이하여야 합니다.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("UTF-8 번역 파일이 아닙니다.") from error
    lines = text.replace("\r\n", "\n").split("\n")
    entries, errors, conflicts = {}, [], set()
    if not lines or lines[0] != HEADER:
        return {"entries": {}, "errors": ["지원하지 않는 번역 파일 머리말입니다."]}
    index = 1
    while index < len(lines):
        if lines[index] == "":
            index += 1
            continue
        start = index + 1
        if lines[index:index + 2] != ["[번역]", "원문:"]:
            errors.append(f"{start}행: [번역] 및 원문: 구분자가 필요합니다.")
            index += 1
            continue
        index += 2
        source, target = [], []
        section = source
        separated = False
        closed = False
        malformed = False
        while index < len(lines):
            line = lines[index]
            if line == "[번역]":
                break
            index += 1
            if line == "[/번역]":
                closed = True
                break
            if line == "번역:" and not separated:
                separated = True
                section = target
            elif line in DELIMITERS:
                malformed = True
            else:
                section.append(line)
        try:
            if not closed or not separated or malformed:
                raise ValueError("번역 블록의 구분자가 잘못되었거나 닫히지 않았습니다.")
            en, ko = _decode(source), _decode(target)
            validate_translation(en, ko)
            if en in conflicts or (en in entries and entries[en] != ko):
                conflicts.add(en)
                entries.pop(en, None)
                raise ValueError(f"같은 원문의 번역이 서로 다릅니다: {en!r}")
            entries[en] = ko
        except ValueError as error:
            errors.append(f"{start}행: {error}")
    return {"entries": entries, "errors": errors}


def choose_file(initial_dir, owner=0):
    """Open the native Windows text picker; cancel returns None."""
    import os
    # Frozen game Python omits ctypes.wintypes; preserve Windows ABI widths.
    class wintypes:
        DWORD = ctypes.c_uint32
        WORD = ctypes.c_uint16
        BOOL = ctypes.c_int32
        HWND = HINSTANCE = ctypes.c_void_p
        LPCWSTR = LPWSTR = ctypes.c_wchar_p
        LPARAM = ctypes.c_ssize_t
    if os.name != "nt":
        raise OSError("파일 선택 창은 Windows에서만 지원됩니다.")

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD)]

    buffer = ctypes.create_unicode_buffer(32768)
    options = OPENFILENAMEW()
    options.lStructSize = ctypes.sizeof(options)
    options.hwndOwner = owner
    options.lpstrFilter = "번역 텍스트 (*.txt)\0*.txt\0\0"
    options.nFilterIndex = 1
    options.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    options.nMaxFile = len(buffer)
    options.lpstrInitialDir = str(Path(initial_dir).resolve())
    options.lpstrTitle = "가져올 번역 파일 선택"
    options.lpstrDefExt = "txt"
    options.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008
    library = ctypes.WinDLL("comdlg32", use_last_error=True)
    library.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    library.GetOpenFileNameW.restype = wintypes.BOOL
    library.CommDlgExtendedError.restype = wintypes.DWORD
    if library.GetOpenFileNameW(ctypes.byref(options)):
        return Path(buffer.value)
    error = library.CommDlgExtendedError()
    if error:
        raise OSError(error, "번역 파일 선택 창을 열지 못했습니다.")
    return None


def open_folder(path):
    import os
    os.startfile(str(Path(path).resolve()))
