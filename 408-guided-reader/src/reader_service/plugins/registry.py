"""插件注册表：扫描 / 校验 / 启停 / 安装 / 卸载。

两个根：
  1. 随包根（只读）：安装目录下的 `plugins/`，可停用、不可卸载；
  2. 用户根（可写）：`%APPDATA%/LearningTools/plugins/`，可安装、可卸载（进回收站）。

状态单独存 `<data-dir>/plugins-state.json`，不写进插件目录——这样升级覆盖随包插件时
不会把用户的启用/停用选择弄丢。

安全边界（与方案 §3.4 对应）：
  * manifest 必须是 JSON 对象，`id` 必须等于目录名；
  * `entry` / `icon` 只能是插件目录内的相对路径，解析后不得跳出插件目录；
  * 静态服务逐个 `resolve()` 校验，禁目录穿越、禁 dotfile、禁白名单外扩展名；
  * 安装 zip 拒绝绝对路径、`..`、符号链接条目；
  * 卸载只动用户根，且是移动到 `<data-dir>/plugins-trash/<时间戳>-<id>/`，不是删除。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from reader_service.appbase import find_bundled_dir

ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
# 选区动作 id 全在插件内部，1 个字母也允许（与插件 id 的 2 位下限区分开）
ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# 选区动作 id 全在插件内部，1 个字母也允许（与插件 id 的 2 位下限区分开）
ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MANIFEST_NAME = "plugin.json"
STATE_NAME = "plugins-state.json"
TRASH_NAME = "plugins-trash"
MAX_MANIFEST_BYTES = 64 * 1024
# 单插件上限（方案 §3.3：单插件 ≤ 300MB）
MAX_PLUGIN_BYTES = 300 * 1024 * 1024
# 静态服务白名单后缀（其余一律 404，避免把其它类型的文件当下载源）
ASSET_SUFFIXES = {
    ".html", ".htm", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".woff", ".woff2", ".ttf", ".otf", ".txt", ".md", ".wasm",
    ".ico", ".map",
}
SURFACES = {"window", "selection"}
ACTION_CONTEXTS = {"selection"}
DEFAULT_ACTION_MAX_CHARS = 60
MAX_ACTION_MAX_CHARS = 2000
# 与拼音/词典无关的窄标点集合：清洗时只削"裹在词外面的"标点，不动词中间的顿号
EDGE_PUNCTUATION = " \t\r\n　.,;:!?，。；：！？、·…—－-~～\"'“”‘’()（）[]【】《》<>〈〉{}「」『』*#`"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
}


class PluginError(ValueError):
    """插件目录或 manifest 不合法。"""


class PluginNotFound(LookupError):
    """插件不存在。"""


def clean_selection_text(text: str, max_chars: int = DEFAULT_ACTION_MAX_CHARS,
                         strip_punctuation: bool = False) -> str:
    """选区文本清洗：去首尾空白、（可选）削掉首尾标点、截断到 max_chars。

    与前端 `plugin-actions.js` 的 `cleanSelectionText` 保持同一口径——
    两边都只用"首尾剥离"，不用正则全局删标点，避免把「太阳病，脉浮」里的
    逗号一起吃掉。
    """
    if not isinstance(text, str):
        return ""
    value = " ".join(text.split())
    if strip_punctuation:
        value = value.strip(EDGE_PUNCTUATION)
        # 剥完外层可能露出新的外层标点（《「本虚标实」》），循环一次即可
        value = value.strip(EDGE_PUNCTUATION)
    limit = max(1, min(int(max_chars or DEFAULT_ACTION_MAX_CHARS), MAX_ACTION_MAX_CHARS))
    return value[:limit]


@dataclass
class PluginRecord:
    id: str
    name: str
    version: str
    description: str
    root: Path
    source: str                      # "bundled" | "user"
    entry: str
    actions: list[dict] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    category: str = ""
    icon: str | None = None
    window: dict = field(default_factory=dict)
    enabled: bool = True
    problem: str | None = None       # manifest 有问题时的原因（列表里灰显，不崩）

    @property
    def removable(self) -> bool:
        return self.source == "user"

    @property
    def url(self) -> str:
        return f"/plugins/{self.id}/{self.entry}"

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "source": self.source,
            "entry": self.entry,
            "actions": self.actions,
            "surfaces": self.surfaces,
            "category": self.category,
            "icon": self.icon,
            "icon_url": f"/plugins/{self.id}/{self.icon}" if self.icon else None,
            "window": self.window,
            "enabled": self.enabled,
            "removable": self.removable,
            "problem": self.problem,
            "url": self.url,
        }


def _read_manifest(directory: Path) -> dict:
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PluginError(f"缺少 {MANIFEST_NAME}")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise PluginError(f"{MANIFEST_NAME} 过大")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PluginError(f"{MANIFEST_NAME} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PluginError(f"{MANIFEST_NAME} 必须是 JSON 对象")
    return data


def _inside(directory: Path, relative: str) -> Path:
    """把相对路径解析到插件目录内；越界或不存在即抛错。"""
    if not isinstance(relative, str) or not relative.strip():
        raise PluginError("路径不能为空")
    if relative.startswith(("/", "\\")) or ":" in relative or "#" in relative:
        raise PluginError(f"只接受插件目录内的相对路径：{relative}")
    root = directory.resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents and candidate != root:
        raise PluginError(f"路径跳出插件目录：{relative}")
    if any(part.startswith(".") for part in candidate.relative_to(root).parts):
        raise PluginError(f"不允许引用隐藏文件：{relative}")
    return candidate


def _clean_entry(directory: Path, entry: str) -> str:
    """entry 必须是插件目录内存在的相对路径。"""
    candidate = _inside(directory, entry)
    if not candidate.is_file():
        raise PluginError(f"entry 文件不存在：{entry}")
    if candidate.suffix.lower() not in {".html", ".htm"}:
        raise PluginError("entry 必须是 .html/.htm")
    return candidate.relative_to(directory.resolve()).as_posix()


def _clean_icon(directory: Path, icon) -> str | None:
    if icon is None or icon == "":
        return None
    candidate = _inside(directory, str(icon))
    if not candidate.is_file():
        raise PluginError(f"icon 文件不存在：{icon}")
    if candidate.suffix.lower() not in {".svg", ".png", ".ico", ".webp", ".jpg", ".jpeg"}:
        raise PluginError("icon 必须是 svg/png/ico/webp/jpg")
    return candidate.relative_to(directory.resolve()).as_posix()


def _clean_surfaces(raw, actions: list[dict]) -> list[str]:
    if raw is None:
        listed = ["window"]
    elif isinstance(raw, str):
        listed = [raw]
    elif isinstance(raw, list):
        listed = raw
    else:
        raise PluginError("surface 必须是数组")
    clean: list[str] = []
    for item in listed:
        if not isinstance(item, str) or item not in SURFACES:
            raise PluginError(f"surface 只支持 window/selection，收到 {item!r}")
        if item not in clean:
            clean.append(item)
    if actions and "selection" not in clean:
        clean.append("selection")
    if not clean:
        clean.append("window")
    return clean


def _clean_window(raw, name: str) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PluginError("window 必须是对象")
    def _dim(key: str, default: int, low: int, high: int) -> int:
        value = raw.get(key, default)
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise PluginError(f"window.{key} 必须是整数") from None
        return max(low, min(number, high))
    resizable = raw.get("resizable", True)
    return {
        "width": _dim("width", 980, 320, 4096),
        "height": _dim("height", 720, 240, 4096),
        "title": str(raw.get("title") or name),
        "resizable": bool(resizable),
    }


def _clean_actions(raw) -> list[dict]:
    """校验选区动作。既接受 `selection.actions`，也接受顶层 `actions`（早期写法）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PluginError("selection.actions 必须是数组")
    cleaned: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PluginError(f"selection.actions[{index}] 必须是对象")
        action_id = item.get("id")
        label = item.get("label")
        if not isinstance(action_id, str) or not ACTION_ID_PATTERN.match(action_id):
            raise PluginError(f"selection.actions[{index}].id 不合法")
        if action_id in seen:
            raise PluginError(f"selection.actions 里 id 重复：{action_id}")
        seen.add(action_id)
        if not isinstance(label, str) or not label.strip():
            raise PluginError(f"selection.actions[{index}].label 不能为空")
        context = item.get("context") or "selection"
        if context not in ACTION_CONTEXTS:
            raise PluginError(f"selection.actions[{index}].context 只支持 selection")
        try:
            order = int(item.get("order", 100))
        except (TypeError, ValueError):
            raise PluginError(f"selection.actions[{index}].order 必须是整数") from None
        try:
            max_chars = int(item.get("maxChars", DEFAULT_ACTION_MAX_CHARS))
        except (TypeError, ValueError):
            raise PluginError(f"selection.actions[{index}].maxChars 必须是整数") from None
        accepts = item.get("accepts") or ["text"]
        if not isinstance(accepts, list) or [str(one) for one in accepts] != ["text"]:
            raise PluginError(f"selection.actions[{index}].accepts 目前只支持 [\"text\"]")
        cleaned.append({
            "id": action_id,
            "label": label.strip()[:16],
            "title": str(item.get("title") or "").strip()[:120],
            "context": context,
            "order": max(0, min(order, 99)),
            "maxChars": max(1, min(max_chars, MAX_ACTION_MAX_CHARS)),
            "stripPunctuation": bool(item.get("stripPunctuation", False)),
        })
    return sorted(cleaned, key=lambda action: (action["order"], action["id"]))


def _record_from_directory(directory: Path, source: str) -> PluginRecord:
    """读取一个插件目录；manifest 有问题时返回带 problem 的记录而不是抛错。"""
    plugin_id = directory.name
    try:
        if plugin_id == TRASH_NAME or plugin_id.startswith("."):
            raise PluginError("目录名不能作为插件 id")
        manifest = _read_manifest(directory)
        if not ID_PATTERN.match(plugin_id):
            raise PluginError("目录名不是合法插件 id（小写字母/数字/短横线，2–32 位）")
        declared = manifest.get("id")
        if declared and declared != plugin_id:
            raise PluginError(f"manifest.id（{declared}）与目录名不一致")
        name = str(manifest.get("name") or plugin_id).strip() or plugin_id
        entry = _clean_entry(directory, manifest.get("entry") or "index.html")
        selection = manifest.get("selection")
        raw_actions = None
        if isinstance(selection, dict):
            raw_actions = selection.get("actions")
        elif selection is not None:
            raise PluginError("selection 必须是对象")
        if raw_actions is None:
            raw_actions = manifest.get("actions")
        actions = _clean_actions(raw_actions)
        return PluginRecord(
            id=plugin_id,
            name=name,
            version=str(manifest.get("version") or "0.0.0"),
            description=str(manifest.get("description") or ""),
            root=directory,
            source=source,
            entry=entry,
            actions=actions,
            surfaces=_clean_surfaces(manifest.get("surface"), actions),
            category=str(manifest.get("category") or ""),
            icon=_clean_icon(directory, manifest.get("icon")),
            window=_clean_window(manifest.get("window"), name),
        )
    except PluginError as exc:
        return PluginRecord(
            id=plugin_id, name=plugin_id, version="?", description="",
            root=directory, source=source, entry="index.html",
            actions=[], surfaces=["window"], category="", icon=None,
            window=_clean_window(None, plugin_id), problem=str(exc),
        )


class PluginRegistry:
    def __init__(self, data_dir: Path, bundled_root: Path | None = None,
                 user_root: Path | None = None):
        self.data_dir = Path(data_dir)
        self.bundled_root = Path(bundled_root) if bundled_root else self._default_bundled_root()
        self.user_root = Path(user_root) if user_root else self._default_user_root()
        self.state_path = self.data_dir / STATE_NAME
        self.trash_root = self.data_dir / TRASH_NAME

    # ---------- 路径与状态 ----------

    @staticmethod
    def _default_bundled_root() -> Path:
        override = os.environ.get("GUIDED_READER_PLUGINS_DIR")
        if override:
            return Path(override)
        # 优先走 appbase（兼容 PyInstaller onedir 的 _internal 布局），
        # 都没命中再退回旧的 parents[3] 约定（开发模式）。
        found = find_bundled_dir("plugins")
        if found is not None:
            return found
        return Path(__file__).resolve().parents[3] / "plugins"

    @staticmethod
    def _default_user_root() -> Path:
        override = os.environ.get("GUIDED_READER_USER_PLUGINS_DIR")
        if override:
            return Path(override)
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "LearningTools" / "plugins"
        return Path.home() / "AppData" / "Roaming" / "LearningTools" / "plugins"

    def _state(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        plugins = raw.get("plugins")
        return plugins if isinstance(plugins, dict) else {}

    def _write_state(self, plugins: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "plugins": plugins}
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    # ---------- 扫描 ----------

    def _scan_root(self, root: Path, source: str) -> Iterable[PluginRecord]:
        if not root.is_dir():
            return []
        records: list[PluginRecord] = []
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            # 回收站与安装中的临时目录（`.xxx.installing`）不算插件
            if directory.name == TRASH_NAME or directory.name.startswith("."):
                continue
            records.append(_record_from_directory(directory, source))
        return records

    def list(self) -> list[PluginRecord]:
        state = self._state()
        merged: dict[str, PluginRecord] = {}
        # 先放随包根，再用用户根覆盖同名 id（用户装的版本优先）
        for record in list(self._scan_root(self.bundled_root, "bundled")) + list(
                self._scan_root(self.user_root, "user")):
            merged[record.id] = record
        result = []
        for record in merged.values():
            saved = state.get(record.id) or {}
            record.enabled = bool(saved.get("enabled", True))
            result.append(record)
        result.sort(key=lambda item: (item.source != "bundled", item.id))
        return result

    def get(self, plugin_id: str) -> PluginRecord:
        for record in self.list():
            if record.id == plugin_id:
                return record
        raise PluginNotFound(f"没有这个插件：{plugin_id}")

    # ---------- 启停 ----------

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        record = self.get(plugin_id)
        if record.problem:
            raise PluginError(f"插件损坏，无法启停：{record.problem}")
        state = self._state()
        entry = dict(state.get(plugin_id) or {})
        entry["enabled"] = bool(enabled)
        state[plugin_id] = entry
        self._write_state(state)
        record.enabled = bool(enabled)
        return record

    # ---------- 静态资源 ----------

    def asset(self, plugin_id: str, relative: str) -> tuple[Path, str]:
        """返回 (文件路径, content-type)；越界、隐藏文件或类型不在白名单则抛错。"""
        if not ID_PATTERN.match(plugin_id or ""):
            raise PluginNotFound("插件 id 不合法")
        record = self.get(plugin_id)
        try:
            target = _inside(record.root, relative)
        except PluginError as exc:
            raise PluginNotFound(str(exc)) from exc
        if not target.is_file():
            raise PluginNotFound("文件不存在")
        if target.suffix.lower() not in ASSET_SUFFIXES:
            raise PluginNotFound("不支持的文件类型")
        return target, CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")

    # ---------- 安装 / 卸载 ----------

    def install_zip(self, payload: bytes) -> PluginRecord:
        if not payload:
            raise PluginError("插件包是空的")
        if len(payload) > MAX_PLUGIN_BYTES:
            raise PluginError("插件包超过 300MB 上限")
        self.user_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="plugin-install-") as temp:
            archive = Path(temp) / "plugin.zip"
            archive.write_bytes(payload)
            extract_root = Path(temp) / "extract"
            extract_root.mkdir()
            try:
                with zipfile.ZipFile(archive) as bundle:
                    for info in bundle.infolist():
                        name = info.filename.replace("\\", "/")
                        if name.startswith("/") or ".." in Path(name).parts:
                            raise PluginError(f"压缩包里有非法路径：{info.filename}")
                        if (info.external_attr >> 16) & 0o170000 == 0o120000:
                            raise PluginError("压缩包里有符号链接，拒绝安装")
                    bundle.extractall(extract_root)
            except zipfile.BadZipFile as exc:
                raise PluginError(f"不是有效的 zip 文件：{exc}") from exc
            candidate = self._normalize_candidate_name(
                self._locate_plugin_dir(extract_root), extract_root)
            self._validate_install_candidate(candidate)
            return self._commit_install(candidate)

    def install_directory(self, source: Path) -> PluginRecord:
        src = Path(source)
        if not src.is_dir():
            raise PluginError(f"目录不存在：{src}")
        candidate = self._locate_plugin_dir(Path(src))
        self._validate_install_candidate(candidate)
        return self._commit_install(candidate)

    @staticmethod
    def _normalize_candidate_name(candidate: Path, root: Path) -> Path:
        """zip 把 plugin.json 放在根时，解出来的目录名是随机临时名，按 id 改名。"""
        if candidate != root and ID_PATTERN.match(candidate.name):
            return candidate
        manifest = _read_manifest(candidate)
        plugin_id = manifest.get("id")
        if not isinstance(plugin_id, str) or not ID_PATTERN.match(plugin_id):
            raise PluginError(
                "压缩包根目录不是合法插件 id，且 plugin.json 里没有声明 id"
            )
        target = candidate.parent / plugin_id
        if target.exists():
            shutil.rmtree(target)
        candidate.rename(target)
        return target

    def _validate_install_candidate(self, candidate: Path) -> None:
        plugin_id = candidate.name
        if not ID_PATTERN.match(plugin_id):
            raise PluginError("插件目录名不是合法 id（小写字母/数字/短横线，2–32 位）")
        manifest = _read_manifest(candidate)
        if manifest.get("id") and manifest["id"] != plugin_id:
            raise PluginError("manifest.id 与目录名不一致")
        record = _record_from_directory(candidate, "user")
        if record.problem:
            raise PluginError(record.problem)
        total = 0
        for path in candidate.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                if total > MAX_PLUGIN_BYTES:
                    raise PluginError("插件体积超过 300MB 上限")

    def _commit_install(self, candidate: Path) -> PluginRecord:
        plugin_id = candidate.name
        existing = next((item for item in self.list()
                         if item.id == plugin_id and item.removable), None)
        if existing is not None:
            self._trash(existing)
        target = self.user_root / plugin_id
        if target.exists():
            shutil.rmtree(target)
        # 先复制到同级临时目录，再原子改名，避免半截目录被扫描到
        staging = self.user_root / f".{plugin_id}.installing"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(candidate, staging)
        staging.replace(target)
        return self.get(plugin_id)

    @staticmethod
    def _locate_plugin_dir(root: Path) -> Path:
        if (root / MANIFEST_NAME).is_file():
            return root
        candidates = [p for p in root.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file()]
        if len(candidates) != 1:
            raise PluginError("压缩包里找不到唯一的插件目录")
        return candidates[0]

    def _trash(self, record: PluginRecord) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.trash_root.mkdir(parents=True, exist_ok=True)
        target = self.trash_root / f"{stamp}-{record.id}"
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.trash_root / f"{stamp}-{record.id}-{suffix}"
        shutil.move(str(record.root), str(target))
        state = self._state()
        state.pop(record.id, None)
        self._write_state(state)
        return target

    def uninstall(self, plugin_id: str) -> Path:
        record = self.get(plugin_id)
        if not record.removable:
            raise PluginError("随包插件只能停用，不能卸载")
        return self._trash(record)

    # ---------- 选区动作 ----------

    def selection_actions(self, include_disabled: bool = False) -> list[dict]:
        actions = []
        for record in self.list():
            if record.problem or "selection" not in record.surfaces:
                continue
            if not record.enabled and not include_disabled:
                continue
            for action in record.actions:
                if action.get("context") != "selection":
                    continue
                actions.append({
                    **action,
                    "plugin": record.id,
                    "plugin_name": record.name,
                    "enabled": record.enabled,
                })
        return sorted(actions, key=lambda item: (item["order"], item["plugin"], item["id"]))
