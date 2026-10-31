"""应用资源基目录定位（2.0 装机验收修复）。

开发模式：模块位于 ``<仓库根>/src/reader_service/``，仓库根即 ``parents[2]``。
PyInstaller onedir：数据文件落在 ``<安装目录>/reader-service/_internal/``，
运行期 ``sys._MEIPASS`` 正指向该目录；exe 目录本身不含随包资源。

历史教训：``Path(__file__).resolve().parents[3]`` 在 frozen 下解析到
exe 目录而非 ``_internal``，导致随包插件（plugins/）与种子技能
（default-skills/）双双定位失败（2026-10-31 装机验收实测）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def candidate_bases() -> list[Path]:
    """按优先级返回可能存放随包资源（plugins/、default-skills/）的基目录。"""
    bases: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass))
        bases.append(Path(sys.executable).resolve().parent)
    else:
        here = Path(__file__).resolve()
        bases.append(here.parents[2])  # 仓库根（与旧 parents[3] 等价）
        bases.append(here.parents[1])  # src/（容错：资源放进 src 时）
    return bases


def find_bundled_dir(name: str) -> Path | None:
    """在候选基目录下查找名为 ``name`` 的随包目录，命中即返回。"""
    for base in candidate_bases():
        candidate = base / name
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None
