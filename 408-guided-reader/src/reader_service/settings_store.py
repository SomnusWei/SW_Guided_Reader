"""`<data-dir>/settings.json`：主题、PDF 库根目录、引导标记。

为什么要单独一个文件而不是塞进 sqlite：
  * 这些值要在**第一屏**就被读到（首屏防闪要同步知道主题），走 sqlite 得先起服务；
  * 它们是"这台机器上的偏好"，不随书库迁移，丢了也不影响数据完整性；
  * 与 v27 的 `settings` 表互为镜像时，文件是真相、表是缓存，冲突时以文件为准。

写盘一律「临时文件 + 原子替换」，避免掉电写出半截 JSON 导致下次启动读不出主题。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

FILE_NAME = "settings.json"
THEMES = ("light", "dark", "system")
DEFAULT_THEME = "system"
# 明确的系统目录黑名单（与资料库挂载的判定保持同一口径）
FORBIDDEN_ROOTS = (
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
)


class SettingsError(ValueError):
    """设置值不合法。"""


def _resolve(path: str | os.PathLike) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()


def validate_library_root(path: str) -> Path:
    """PDF 库根目录校验：绝对路径、非系统目录、能建能写。**不建目录、不动文件**。"""
    if not isinstance(path, str) or not path.strip():
        raise SettingsError("请提供目录路径。")
    raw = Path(os.path.expandvars(os.path.expanduser(path.strip())))
    if not raw.is_absolute():
        raise SettingsError("请提供绝对路径。")
    resolved = raw.resolve()
    if resolved.parent == resolved:
        raise SettingsError("不能把磁盘根目录当作书库。")
    lowered = str(resolved).lower()
    for prefix in FORBIDDEN_ROOTS:
        if lowered == prefix or lowered.startswith(prefix + os.sep):
            raise SettingsError("不能把系统目录当作书库。")
    if resolved.exists():
        if not resolved.is_dir():
            raise SettingsError("该路径不是目录。")
        if not os.access(resolved, os.W_OK):
            raise SettingsError("该目录不可写。")
    else:
        parent = resolved.parent
        if not parent.is_dir():
            raise SettingsError("上级目录不存在。")
        if not os.access(parent, os.W_OK):
            raise SettingsError("上级目录不可写，无法创建书库目录。")
    return resolved


class ReaderSettings:
    """settings.json 的读写与校验。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / FILE_NAME

    # ---------- 存储 ----------

    def load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, **data}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 主题 ----------

    def theme(self) -> str:
        value = (self.load().get("appearance") or {}).get("theme")
        return value if value in THEMES else DEFAULT_THEME

    def set_theme(self, theme: str) -> str:
        if theme not in THEMES:
            raise SettingsError("界面模式只支持 light / dark / system。")
        data = self.load()
        appearance = dict(data.get("appearance") or {})
        appearance["theme"] = theme
        data["appearance"] = appearance
        self._save(data)
        return theme

    # ---------- PDF 库根目录 ----------

    def default_library_root(self) -> Path:
        return self.data_dir / "blobs"

    def library_root(self) -> Path:
        value = (self.load().get("library") or {}).get("root")
        if not isinstance(value, str) or not value.strip():
            return self.default_library_root()
        try:
            return _resolve(value)
        except OSError:
            return self.default_library_root()

    def set_library_root(self, path: str) -> Path:
        resolved = validate_library_root(path)
        data = self.load()
        library = dict(data.get("library") or {})
        library["root"] = str(resolved)
        library["changed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["library"] = library
        self._save(data)
        return resolved

    def library_stats(self) -> dict:
        """当前库目录有多少文件、占多大——用于"已有 N 本书仍在原目录"的提示。"""
        root = self.library_root()
        files = 0
        total = 0
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    files += 1
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        usage = shutil.disk_usage(root if root.is_dir() else self.data_dir)
        return {
            "root": str(root),
            "default_root": str(self.default_library_root()),
            "is_default": root == self.default_library_root(),
            "files": files,
            "bytes": total,
            "free_bytes": usage.free,
        }

    # ---------- 引导标记 ----------

    def onboarding_done(self) -> bool:
        onboarding = self.load().get("onboarding")
        return bool(isinstance(onboarding, dict) and onboarding.get("done"))

    def set_onboarding_done(self, done: bool = True) -> None:
        data = self.load()
        onboarding = dict(data.get("onboarding") or {})
        onboarding["done"] = bool(done)
        onboarding["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["onboarding"] = onboarding
        self._save(data)
