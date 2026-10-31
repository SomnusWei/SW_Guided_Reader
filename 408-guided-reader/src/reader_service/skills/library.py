"""资料库挂载登记（方案 §6.5）：只记路径，不复制任何文件。

2.0 里这只是一个**状态标记**——Reader 还没有"读资料"的消费方（AI 不把教材塞进
提示词，也没有"打开参考文件"的入口）。它的作用是：

  * 让用户在设置里看到"资料库：已挂载 / 未挂载 / 路径不可达"；
  * 给 2.1 的教材检索（§13）留一个可读的登记点（`library.json`）。

所以这里刻意不做索引、不做文件复制、不改 `SkillRegistry` 的扫描逻辑。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LIBRARY_NAME = "library.json"
# 资料层大目录（用于在状态里显示"中医教材 711 部"这类信息）
TEXTBOOK_DIRS = ("中医教材", "西医教材", "石学敏体系", "倪海厦体系", "蒸馏产出")
COUNTED_SUFFIXES = {".txt", ".pdf", ".md", ".docx"}
MAX_COUNTED_FILES = 20000
# 明确拒绝的路径前缀（系统目录，挂上去只会误导用户）
FORBIDDEN_PREFIXES = (
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
)


class LibraryMountError(ValueError):
    """挂载路径不合法。"""


def _resolve(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()


def _count_files(directory: Path) -> int:
    total = 0
    for _, _, files in os.walk(directory):
        total += len(files)
        if total >= MAX_COUNTED_FILES:
            return MAX_COUNTED_FILES
    return total


def _textbook_report(root: Path) -> list[dict]:
    report = []
    for name in TEXTBOOK_DIRS:
        candidate = root / name
        if candidate.is_dir():
            report.append({"name": name, "files": _count_files(candidate)})
    return report


def validate_mount_path(path: str) -> Path:
    """校验挂载路径：绝对路径、存在、是技能目录、不是系统目录。"""
    if not isinstance(path, str) or not path.strip():
        raise LibraryMountError("请提供资料目录路径。")
    raw = Path(os.path.expandvars(os.path.expanduser(path.strip())))
    if not raw.is_absolute():
        raise LibraryMountError("请提供绝对路径。")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise LibraryMountError(f"路径不存在或无法访问：{exc}") from exc
    if not resolved.is_dir():
        raise LibraryMountError("路径不是目录。")
    # 盘符根（C:\、D:\）与系统目录一律拒绝：既没有意义，也容易被误挂
    if resolved.parent == resolved:
        raise LibraryMountError("不能挂载磁盘根目录。")
    lowered = str(resolved).lower()
    for prefix in FORBIDDEN_PREFIXES:
        if lowered == prefix or lowered.startswith(prefix + os.sep):
            raise LibraryMountError("不能挂载系统目录。")
    if not (resolved / "SKILL.md").is_file():
        raise LibraryMountError("该目录下没有 SKILL.md，不是技能目录。")
    return resolved


class LibraryMount:
    """`<data-dir>/skills/library.json` 的读写与状态聚合。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "skills" / LIBRARY_NAME

    # ---------- 存储 ----------

    def load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        clean = {}
        for name, entry in raw.items():
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                dirs = entry.get("textbook_dirs")
                clean[str(name)] = {
                    "path": entry["path"],
                    # §13.13 #1：2.1 直接读这个字段找语料根，不再猜目录名
                    "textbook_dirs": [str(item) for item in dirs] if isinstance(dirs, list) else [],
                    "added_at": str(entry.get("added_at") or ""),
                }
        return clean

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 状态 ----------

    def status(self, name: str) -> dict:
        entry = self.load().get(name)
        if not entry:
            return {
                "mounted": False, "path": None, "reachable": False,
                "textbooks": [], "textbook_dirs": [],
            }
        path = Path(entry["path"])
        reachable = path.is_dir()
        return {
            "mounted": True,
            "path": entry["path"],
            "added_at": entry["added_at"],
            "reachable": reachable,
            "textbooks": _textbook_report(path) if reachable else [],
            "textbook_dirs": list(entry.get("textbook_dirs") or []),
            "message": "" if reachable else "路径不可达（不影响任何功能）",
        }

    def all_status(self) -> dict[str, dict]:
        return {name: self.status(name) for name in self.load()}

    # ---------- 变更 ----------

    def mount(self, name: str, path: str) -> dict:
        resolved = validate_mount_path(path)
        data = self.load()
        data[name] = {
            "path": str(resolved),
            # 登记时就把"资料层目录名"记下来（§13.13 #1），2.1 直接用，不必再猜
            "textbook_dirs": [item["name"] for item in _textbook_report(resolved)],
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save(data)
        return self.status(name)

    def unmount(self, name: str) -> dict:
        data = self.load()
        if name in data:
            del data[name]
            self._save(data)
        return self.status(name)
