"""Windows 原生「另存为」对话框（ctypes comdlg32，无第三方依赖）。

桌面外壳下导出笔记时，用户不一定有默认「下载」文件夹（可能被移走或重定向），
所以由本地服务弹一个系统另存为对话框让用户自选保存位置。冻结包里 ctypes
运行时加载 comdlg32.dll，无需 PyInstaller 额外配置。非 Windows 返回 None，
调用方自行回落到「下载」文件夹通道。
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

_OFN_OVERWRITEPROMPT = 0x0002
_OFN_HIDEREADONLY = 0x0004
_OFN_NOCHANGEDIR = 0x0008
_OFN_PATHMUSTEXIST = 0x0800
_OFN_FILEMUSTEXIST = 0x1000


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", ctypes.c_ulong),
        ("hwndOwner", ctypes.c_void_p),
        ("hInstance", ctypes.c_void_p),
        ("lpstrFilter", ctypes.c_wchar_p),
        ("lpstrCustomFilter", ctypes.c_wchar_p),
        ("nMaxCustFilter", ctypes.c_ulong),
        ("nFilterIndex", ctypes.c_ulong),
        ("lpstrFile", ctypes.c_wchar_p),
        ("nMaxFile", ctypes.c_ulong),
        ("lpstrFileTitle", ctypes.c_wchar_p),
        ("nMaxFileTitle", ctypes.c_ulong),
        ("lpstrInitialDir", ctypes.c_wchar_p),
        ("lpstrTitle", ctypes.c_wchar_p),
        ("Flags", ctypes.c_ulong),
        ("nFileOffset", ctypes.c_ushort),
        ("nFileExtension", ctypes.c_ushort),
        ("lpstrDefExt", ctypes.c_wchar_p),
        ("lCustData", ctypes.c_void_p),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", ctypes.c_wchar_p),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", ctypes.c_ulong),
        ("FlagsEx", ctypes.c_ulong),
    ]


def _initial_dir() -> str | None:
    """对话框初始目录：优先「下载」，其次用户主目录。"""
    downloads = Path.home() / "Downloads"
    candidate = downloads if downloads.is_dir() else Path.home()
    return str(candidate) if candidate.is_dir() else None


class DialogUnavailable(RuntimeError):
    """当前环境无法弹出「另存为」对话框（非 Windows / 缺 comdlg32）。"""


def ask_save_path(default_filename: str, title: str = "导出笔记") -> Path | None:
    """弹出系统「另存为」对话框；用户确认返回所选路径，取消返回 None。

    环境不支持时抛 DialogUnavailable，由调用方回落到「下载」文件夹通道。
    返回的是完整选择结果，调用方原样写入即可（除无扩展名时补 .md）。
    """
    if os.name != "nt":
        raise DialogUnavailable("仅 Windows 支持系统另存为对话框")
    try:
        comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
        get_save_file_name = comdlg32.GetSaveFileNameW
    except (OSError, AttributeError) as exc:
        raise DialogUnavailable("加载 comdlg32 失败") from exc

    get_save_file_name.argtypes = [ctypes.POINTER(_OPENFILENAMEW)]
    get_save_file_name.restype = ctypes.c_int

    buffer = ctypes.create_unicode_buffer(1024)
    buffer.value = default_filename
    initial = _initial_dir()
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = None
    ofn.lpstrFilter = "Markdown 文件 (*.md)\0*.md\0所有文件 (*.*)\0*.*\0"
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buffer, ctypes.c_wchar_p)
    ofn.nMaxFile = len(buffer)
    ofn.lpstrInitialDir = initial
    ofn.lpstrTitle = title
    ofn.lpstrDefExt = "md"
    ofn.Flags = (
        _OFN_OVERWRITEPROMPT | _OFN_HIDEREADONLY | _OFN_NOCHANGEDIR
        | _OFN_PATHMUSTEXIST
    )
    if not get_save_file_name(ctypes.byref(ofn)):
        return None
    chosen = buffer.value.strip()
    if not chosen:
        return None
    return Path(chosen)
