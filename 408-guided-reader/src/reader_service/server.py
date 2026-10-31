from __future__ import annotations

import hmac
import json
import shutil
import logging
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlparse

from reader_service.agent_runtime import (
    PROVIDER_PRESETS,
    ProviderFailure,
    StreamConsumerDisconnected,
    delete_provider_credential,
    read_provider_credential,
    write_provider_credential,
)
from reader_service.annotation import (
    AnnotationService,
    AnnotationSyncConfig,
    PdfAnnotationSync,
)
from reader_service.assistant import AssistantService, AssistantStateError
from reader_service.export_md import (
    MarkdownExporter, save_markdown_to_downloads, save_markdown_to_path,
)
from reader_service.native_dialog import DialogUnavailable, ask_save_path
from reader_service.library import IntakeError, LibraryService
from reader_service.jobs import PreparationCoordinator
from reader_service.outline import OutlineService
from reader_service.saved_explanations import (
    SavedExplanationError,
    SavedExplanationService,
)
from reader_service.plugins import (
    PluginError,
    PluginNotFound,
    PluginRegistry,
)
from reader_service.skills import (
    BUILTIN_SKILL_NAME,
    LibraryMount,
    LibraryMountError,
    SkillInstaller,
    SkillRegistry,
    human_size,
    restore_skill,
    seed_default_skills,
    seed_skill_names,
)
from reader_service.skills.seed import directory_size
from reader_service.foundation.umi_ocr import umi_availability
from reader_service.storage import ManagedPaths
from reader_service.memory import LearningMemory
from reader_service.settings_store import ReaderSettings, SettingsError
from reader_service.notes import NotesService
from reader_service.visual_region import render_visual_region

logger = logging.getLogger(__name__)


_REVISION_PDF = re.compile(r"^/api/revisions/([0-9a-f-]+)/pdf$")
_REVISION_COVER = re.compile(r"^/api/revisions/([0-9a-f-]+)/cover$")
_REVISION_POSITION = re.compile(r"^/api/revisions/([0-9a-f-]+)/position$")
_REVISION_PREPARATION = re.compile(r"^/api/revisions/([0-9a-f-]+)/preparation$")
_REVISION_PREPARATION_EVENTS = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/preparation/events$"
)
_REVISION_PREPARATION_RETRY = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/preparation/retry$"
)
_REVISION_OVERLAY = re.compile(r"^/api/revisions/([0-9a-f-]+)/overlay$")
_REVISION_SEARCH = re.compile(r"^/api/revisions/([0-9a-f-]+)/search$")
_REVISION_OUTLINE = re.compile(r"^/api/revisions/([0-9a-f-]+)/outline$")
_REVISION_PAGE_LABELS = re.compile(r"^/api/revisions/([0-9a-f-]+)/page-labels$")
_REVISION_PAGE_LABEL = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/page-labels/(\d+)$"
)
_REVISION_ANNOTATIONS = re.compile(r"^/api/revisions/([0-9a-f-]+)/annotations$")
_REVISION_ANNOTATIONS_SYNC = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/annotations/sync-all$"
)
_REVISION_ANNOTATION = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/annotations/([0-9a-f-]+)$"
)
_REVISION_ANNOTATION_SYNC = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/annotations/([0-9a-f-]+)/sync$"
)
_REVISION_OUTLINE_IMPORT = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/outline/import-bookmarks$"
)
_SKILL = re.compile(r"^/api/skills/([^/]+)$")
_SKILL_ENABLED = re.compile(r"^/api/skills/([^/]+)/enabled$")
_SKILL_LIBRARY = re.compile(r"^/api/skills/([^/]+)/library$")
_SKILL_RESTORE = re.compile(r"^/api/skills/([^/]+)/restore$")
# 插件静态资源：/plugins/<id>/<相对路径>（只读、白名单、禁穿越）
_PLUGIN_ASSET = re.compile(r"^/plugins/([a-z][a-z0-9-]{1,31})/(.+)$")
_PLUGIN_ITEM = re.compile(r"^/api/plugins/([a-z][a-z0-9-]{1,31})$")
_PLUGIN_ENABLED = re.compile(r"^/api/plugins/([a-z][a-z0-9-]{1,31})/enabled$")
MAX_PLUGIN_UPLOAD = 300 * 1024 * 1024

# 自定义快捷键：动作 → 组合键字符串（如 "Ctrl+Alt+H"），空值=未绑定
_SHORTCUT_ACTIONS = ("highlight", "underline", "note", "ask_ai", "textbox", "region", "sticky", "toggle_annotations", "toggle_notes")
_SHORTCUTS_FILENAME = "shortcuts.json"


def _build_time() -> str:
    """构建时间：打包后取 exe 的修改时间，源码运行取本文件时间。"""
    target = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(target.stat().st_mtime))
    except OSError:
        return ""


def _library_candidates() -> list[str]:
    """首启引导「自动检测」用的已知位置，命中即返回（不扫描全盘、不跟随盘符根）。"""
    home = Path.home()
    roots = (
        home / ".workbuddy" / "skills",
        Path(os.environ.get("APPDATA", "")) / "LearningTools" / "skills",
        Path(os.environ.get("LOCALAPPDATA", "")) / "408 Guided Reader" / "skills",
        home / "skills",
    )
    found: list[str] = []
    for root in roots:
        if not str(root).strip() or root == Path(root.anchor):
            continue
        candidate = root / "yixue-zonghe"
        try:
            if (candidate / "SKILL.md").is_file():
                found.append(str(candidate))
        except OSError:
            continue
    return found


def _library_migrate_status(paths: ManagedPaths) -> dict:
    """换书库目录后的迁移预检：默认 blobs 里还有多少文件没搬过去。

    §5.3 的语义：换目录 = 换"以后新书放哪"；迁移是显式动作，文件按
    sha256 命名，搬过去不影响任何数据库记录。
    """
    override = paths.blob_override()
    source = paths.blob_root()
    pending = 0
    bytes_pending = 0
    if override is not None and source.is_dir():
        for item in source.rglob("*.pdf"):
            if not item.is_file():
                continue
            target = override.joinpath(item.relative_to(source))
            if target.is_file():
                continue
            pending += 1
            try:
                bytes_pending += item.stat().st_size
            except OSError:
                continue
    return {
        "source": str(source),
        "target": str(override) if override is not None else None,
        "pending": pending,
        "bytes": bytes_pending,
    }


def _library_migrate_run(paths: ManagedPaths) -> dict:
    """执行迁移：把默认 blobs 里缺的文件搬到外部书库（同名跳过，绝不覆盖）。"""
    override = paths.blob_override()
    source = paths.blob_root()
    if override is None:
        raise ValueError("还没有更换书库目录，无需迁移。")
    moved = 0
    skipped = 0
    failed: list[str] = []
    if source.is_dir():
        for item in source.rglob("*.pdf"):
            if not item.is_file():
                continue
            target = override.joinpath(item.relative_to(source))
            if target.is_file():
                skipped += 1
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(target))
                moved += 1
            except OSError as exc:
                failed.append(f"{item.name}: {exc}")
    return {"moved": moved, "skipped": skipped, "failed": failed}


def _load_shortcuts(root: Path) -> dict[str, str]:
    try:
        data = json.loads((root / _SHORTCUTS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if key in _SHORTCUT_ACTIONS and isinstance(value, str) and value.strip()
    }
_PROVIDER_KEY = re.compile(r"^/api/settings/providers/([^/]+)/key$")
_REVISION_ASSISTANT_ASK = re.compile(r"^/api/revisions/([0-9a-f-]+)/assistant/ask$")
_REVISION_VISUAL_REGION = re.compile(r"^/api/revisions/([0-9a-f-]+)/visual-region$")
_REVISION_ASSISTANT_BAKEOFF = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/assistant/bake-off$"
)
_REVISION_ASSISTANT_SAVE = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/assistant/save$"
)
_REVISION_ASSISTANT_HISTORY = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/assistant/history$"
)
_REVISION_ASSISTANT_HISTORY_ITEM = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/assistant/history/([0-9a-f-]+)$"
)
_REVISION_ANNOTATION_REVIEW = re.compile(
    r"^/api/revisions/([0-9a-f-]+)/annotations/([0-9a-f-]+)/review$"
)
# Markdown 笔记（全局笔记本）
_NOTES = re.compile(r"^/api/notes$")
_NOTES_EXPORT = re.compile(r"^/api/notes/export$")
_NOTE_EXPORT = re.compile(r"^/api/notes/([0-9a-f-]+)/export$")
_NOTE_ITEM = re.compile(r"^/api/notes/([0-9a-f-]+)$")
_BOOK = re.compile(r"^/api/books/([0-9a-f-]+)$")
_SESSION_COOKIE = "reader_launch"


class ReaderServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def handler_factory(
    service: LibraryService,
    token: str,
    project_root: Path | None = None,
    preparation: PreparationCoordinator | None = None,
    annotations: AnnotationService | None = None,
    outline: OutlineService | None = None,
    assistant: AssistantService | None = None,
    saved_explanations: SavedExplanationService | None = None,
    teaching=None,
    annotation_sync: AnnotationSyncConfig | None = None,
    pdf_sync: PdfAnnotationSync | None = None,
    skills: SkillRegistry | None = None,
    exporter: MarkdownExporter | None = None,
    notes: NotesService | None = None,
    plugins: PluginRegistry | None = None,
    seed_source: str | None = None,
) -> Callable[..., BaseHTTPRequestHandler]:
    library_mounts = LibraryMount(skills.data_dir) if skills is not None else None
    static_root = Path(__file__).with_name("static")
    root = project_root or Path(__file__).resolve().parents[2]
    # 打包（PyInstaller）环境下仓库 node_modules 不存在，回退到随包分发的 static/vendor/
    bundled_vendor = static_root.joinpath("vendor")

    def _pick(primary: Path, fallback: Path) -> Path:
        return primary if primary.exists() else fallback

    pdfjs_root = _pick(
        root.joinpath("node_modules", "pdfjs-dist", "build"),
        bundled_vendor.joinpath("pdfjs", "build"),
    )
    marked_root = _pick(
        root.joinpath("node_modules", "marked", "lib"),
        bundled_vendor.joinpath("marked"),
    )
    dompurify_root = _pick(
        root.joinpath("node_modules", "dompurify", "dist"),
        bundled_vendor.joinpath("dompurify"),
    )
    katex_root = _pick(
        root.joinpath("node_modules", "katex", "dist"),
        bundled_vendor.joinpath("katex"),
    )
    memory = LearningMemory(service.database)
    # 测试里的假 service 没有 paths；设置文件就落在临时目录，不影响被测行为。
    settings_root = getattr(getattr(service, "paths", None), "root", None) or Path(
        tempfile.gettempdir()
    ) / "learningtools-settings"
    reader_settings = ReaderSettings(settings_root)
    # The settings route replaces the frozen config; handlers share this slot.
    sync_state = {"config": annotation_sync or AnnotationSyncConfig()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "GuidedReader/0.1"

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            # Validate before dispatch, including static bootstrap and every
            # write entry point. A local cookie must never authorize another
            # website's request to the loopback service.
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if port == 80:
                hosts.update({"127.0.0.1", "localhost"})
            supplied_hosts = self.headers.get_all("Host", [])
            host = supplied_hosts[0].lower() if len(supplied_hosts) == 1 else ""
            origins = self.headers.get_all("Origin", [])
            # 平台主窗口（tauri:// 自定义域）内嵌 iframe 的初次文档导航，以及启动器
            # 主窗口整页跳转到本服务（GET/HEAD 均无 Origin），在 WebView2 新版运行时
            # 下都属于 cross-site（Sec-Fetch-Site: cross-site，Sec-Fetch-Dest:
            # iframe/frame/document）。放行这类只读文档导航，但跨站 fetch / 表单
            # 提交（dest=empty 的调用、任何跨站写入）依旧拒绝，保留本地 CSRF 防护。
            dest = (self.headers.get("Sec-Fetch-Dest") or "").strip().lower()
            frame_nav = dest in {"iframe", "frame", "document"} and self.command in {"GET", "HEAD"}
            if (self.headers.get("Sec-Fetch-Site") == "cross-site" and not frame_nav) or host not in hosts or (origins and (
                len(origins) != 1 or origins[0].lower() != f"http://{host}"
            )):
                self.close_connection = True
                self._json(HTTPStatus.FORBIDDEN, {
                    "code": "UNTRUSTED_LOCAL_ORIGIN", "error": "Reader 只接受当前本地启动地址的请求。"
                })
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if self._reading_request(parsed.path, 'GET'):
                return
            if self._memory_request(parsed.path, 'GET'):
                return
            guide_events = re.fullmatch(r"/api/revisions/([0-9a-f-]+)/sections/([0-9a-f-]+)/guide/events", parsed.path)
            if guide_events:
                if not self._authorized():
                    return
                if teaching is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "导读服务不可用。"})
                    return
                started = False
                try:
                    params = parse_qs(parsed.query)
                    asset_id = _first(params, "asset_id") or ""
                    after = int(_first(params, "after") or -1)
                    revision, section = guide_events.groups()
                    self._start_guide_stream()
                    started = True
                    while True:
                        event = teaching.draft_event(revision, section, asset_id, after)
                        self._guide_stream_event(event)
                        if event["type"] in {"complete", "error"}:
                            return
                        after = event["sequence"]
                except StreamConsumerDisconnected:
                    return
                except (LookupError, TypeError, ValueError) as exc:
                    if not started:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    else:
                        try:
                            self._guide_stream_event({"type": "error", "code": "GUIDE_STREAM_FAILED", "error": str(exc)})
                        except StreamConsumerDisconnected:
                            pass
                    return
            guide = re.fullmatch(r"/api/revisions/([0-9a-f-]+)/sections/([0-9a-f-]+)/guide", parsed.path)
            if guide:
                if not self._authorized():
                    return
                if teaching is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "导读服务不可用。"})
                    return
                try:
                    revision, section = guide.groups()
                    result = teaching.snapshot(revision, section)
                except (LookupError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            if parsed.path == "/api/health":
                if not self._authorized():
                    return
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            context_match = re.fullmatch(r"/api/revisions/([0-9a-f-]+)/reading-context", parsed.path)
            if context_match:
                if not self._authorized():
                    return
                try:
                    revision = service.revision(context_match[1])
                    params = parse_qs(parsed.query)
                    page = int(_first(params, 'page') or 0)
                    y = float(_first(params, 'y') or 0)
                    if not 0 <= page < revision['page_count'] or not 0 <= y <= 1:
                        raise ValueError('阅读位置无效。')
                    result = outline.reading_context(context_match[1], page, y) if outline else {'chapter': None, 'section': None, 'subsection': None}

                except (LookupError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {'error': str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            if _NOTES_EXPORT.fullmatch(parsed.path):
                if not self._authorized() or not self._notes_ready():
                    return
                content, filename = notes.export_all()
                self._markdown_deliver(content, filename, parse_qs(parsed.query, keep_blank_values=True))
                return
            note_export = _NOTE_EXPORT.fullmatch(parsed.path)
            if note_export:
                if not self._authorized() or not self._notes_ready():
                    return
                try:
                    content, filename = notes.export_one(note_export.group(1))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._markdown_deliver(content, filename, parse_qs(parsed.query, keep_blank_values=True))
                return
            if _NOTES.fullmatch(parsed.path):
                if not self._authorized() or not self._notes_ready():
                    return
                params = parse_qs(parsed.query, keep_blank_values=True)
                try:
                    result = notes.list_notes(
                        query=_first(params, "query") or "",
                        book=_first(params, "book") or "",
                        limit=_first(params, "limit") or 50,
                        offset=_first(params, "offset") or 0,
                    )
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            note_item = _NOTE_ITEM.fullmatch(parsed.path)
            if note_item:
                if not self._authorized() or not self._notes_ready():
                    return
                try:
                    note = notes.get(note_item.group(1))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"note": note})
                return
            if parsed.path == "/api/books":
                if not self._authorized():
                    return
                self._json(HTTPStatus.OK, {"books": service.list_books()})
                return
            if parsed.path == "/api/providers":
                if not self._authorized():
                    return
                self._json(HTTPStatus.OK, {"providers": provider_catalog()})
                return
            if parsed.path == "/api/skills":
                if not self._authorized():
                    return
                if skills is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                seeded = set(seed_skill_names(seed_source))
                self._json(HTTPStatus.OK, {"skills": [
                    self._skill_json(record, seeded, library_mounts, skills.data_dir)
                    for record in skills.scan()
                ]})
                return
            if parsed.path == "/api/plugins":
                if not self._authorized():
                    return
                if plugins is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PLUGINS_UNAVAILABLE", "error": "插件服务不可用。"
                    })
                    return
                self._json(HTTPStatus.OK, {
                    "plugins": [record.to_json() for record in plugins.list()],
                    "roots": {
                        "bundled": str(plugins.bundled_root),
                        "user": str(plugins.user_root),
                    },
                })
                return
            if parsed.path == "/api/selection-actions":
                if not self._authorized():
                    return
                actions = plugins.selection_actions() if plugins is not None else []
                self._json(HTTPStatus.OK, {"actions": actions})
                return
            if parsed.path == "/api/settings/appearance":
                if not self._authorized():
                    return
                self._json(HTTPStatus.OK, {
                    "theme": reader_settings.theme(),
                    "themes": ["light", "dark", "system"],
                })
                return
            if parsed.path == "/api/settings/library/migrate":
                if not self._authorized():
                    return
                if not isinstance(service.paths, ManagedPaths):
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "LIBRARY_UNAVAILABLE", "error": "书库服务不可用。"
                    })
                    return
                try:
                    status = _library_migrate_status(service.paths)
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "LIBRARY_STATUS_FAILED", "error": f"迁移状态读取失败：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, status)
                return
            if parsed.path == "/api/settings/library":
                if not self._authorized():
                    return
                stats = reader_settings.library_stats()
                try:
                    stats["books"] = len(service.list_books())
                except Exception:  # 计数失败不该让设置页打不开
                    stats["books"] = None
                self._json(HTTPStatus.OK, stats)
                return
            if parsed.path == "/api/settings/ocr":
                if not self._authorized():
                    return
                try:
                    probe = umi_availability()
                except Exception as exc:  # 探测失败不该让设置页打不开
                    probe = {"backend": "umi-ocr", "available": False, "error": str(exc)}
                self._json(HTTPStatus.OK, {
                    **probe,
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                return
            if parsed.path == "/api/about":
                if not self._authorized():
                    return
                from reader_service import __version__
                self._json(HTTPStatus.OK, {
                    "version": __version__,
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "data_dir": str(service.paths.root),
                    "log_dir": str(service.paths.root / "logs"),
                    "plugin_roots": {
                        "bundled": str(plugins.bundled_root) if plugins else None,
                        "user": str(plugins.user_root) if plugins else None,
                    },
                    "skills_dir": str(service.paths.root / "skills"),
                    "build_time": _build_time(),
                })
                return
            if parsed.path == "/api/onboarding":
                if not self._authorized():
                    return
                # 已有书的用户（含 1.x 升级用户）不再弹引导：引导只为
                # 「全新数据目录、一本书都没有」的首启场景服务。
                onboarding_done = reader_settings.onboarding_done() or bool(
                    service.list_books()
                )
                self._json(HTTPStatus.OK, {
                    "done": onboarding_done,
                    "skills": seed_skill_names(seed_source),
                    "books": len(service.list_books()),
                    # 引导第 2 步的「自动检测」：只找几个已知位置，不做全盘扫描
                    "library_candidates": _library_candidates(),
                })
                return
            if parsed.path == "/api/settings/annotation-sync":
                if not self._authorized():
                    return
                config = sync_state["config"]
                self._json(HTTPStatus.OK, {
                    "mode": config.mode,
                    "target": config.target,
                    "modes": ["off", "manual", "auto"],
                    "targets": ["source", "copy"],
                    "available": pdf_sync is not None,
                })
                return
            if parsed.path == "/api/settings/shortcuts":
                if not self._authorized():
                    return
                self._json(HTTPStatus.OK, {
                    "shortcuts": _load_shortcuts(service.paths.root),
                    "actions": list(_SHORTCUT_ACTIONS),
                })
                return
            if parsed.path == "/api/export/markdown":
                if not self._authorized():
                    return
                if exporter is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "EXPORT_UNAVAILABLE", "error": "导出服务不可用。"
                    })
                    return
                params = parse_qs(parsed.query, keep_blank_values=True)
                revision_id = _first(params, "revision") or ""
                try:
                    content, filename = self._markdown_export(
                        exporter,
                        revision_id,
                        _first(params, "scope") or "",
                        _first(params, "id"),
                        params,
                    )
                except AssistantStateError as exc:
                    self._json(HTTPStatus.CONFLICT, {
                        "code": f"EXPORT_{exc.code}", "error": exc.user_message
                    })
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                if _first(params, "download") == "1":
                    self._markdown_deliver(content, filename, params)
                    return
                archived = None
                try:
                    archived = str(exporter.save_to_data_dir(revision_id, content, filename))
                except (OSError, ValueError):
                    archived = None
                self._json(HTTPStatus.OK, {
                    "filename": filename,
                    "content": content,
                    "archived": archived,
                })
                return
            if parsed.path == "/api/assistant/status":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.OK, {
                        "role": "ASSISTANT", "provider": "deepseek", "model": None,
                        "configured": False, "cooling": False,
                        "credential_target": "408-guided-reader-deepseek",
                        "active_provider": "deepseek", "bakeoff_enabled": False,
                        "providers": [],
                    })
                    return
                self._json(HTTPStatus.OK, assistant.status())
                return
            if parsed.path == "/api/assistant/inspection":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "AI 功能不可用"})
                    return
                self._json(HTTPStatus.OK, assistant.inspect_payloads())
                return
            match = _REVISION_OUTLINE.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if outline is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Outline is unavailable"})
                    return
                try:
                    result = (outline.stored_snapshot(match.group(1))
                              if _first(parse_qs(parsed.query), "stored") == "1"
                              else outline.bootstrap(match.group(1)))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            match = _REVISION_PAGE_LABELS.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if outline is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Page labels are unavailable"})
                    return
                try:
                    result = outline.page_label_snapshot(match.group(1))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            match = _REVISION_SEARCH.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if preparation is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Search is unavailable"})
                    return
                try:
                    query_text = _first(parse_qs(parsed.query, keep_blank_values=True), "q") or ""
                    result = preparation.foundation.search(match.group(1), query_text)
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            match = _REVISION_ANNOTATIONS.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if annotations is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Annotations are unavailable"})
                    return
                try:
                    page_value = _first(parse_qs(parsed.query), "page")
                    values = (annotations.list_page(match.group(1), int(page_value))
                              if page_value is not None
                              else annotations.list_all(match.group(1)))
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"annotations": values})
                return
            history_match = _REVISION_ASSISTANT_HISTORY.fullmatch(parsed.path)
            if history_match:
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Assistant is unavailable"})
                    return
                self._json(
                    HTTPStatus.OK,
                    {"history": assistant.history_list(history_match.group(1))},
                )
                return
            history_item = _REVISION_ASSISTANT_HISTORY_ITEM.fullmatch(parsed.path)
            if history_item:
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Assistant is unavailable"})
                    return
                try:
                    payload = assistant.archived_root(history_item.group(2))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"conversation": payload})
                return
            match = _REVISION_PREPARATION_EVENTS.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if preparation is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Preparation is unavailable"})
                    return
                self._preparation_events(preparation, match.group(1))
                return
            match = _REVISION_PREPARATION.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if preparation is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Preparation is unavailable"})
                    return
                try:
                    statuses = preparation.foundation.statuses(match.group(1))
                    counts = preparation.jobs.counts(match.group(1))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {
                    "pages": statuses,
                    "jobs": counts,
                    "ocr": umi_availability(),
                })
                return
            match = _REVISION_OVERLAY.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                if preparation is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Preparation is unavailable"})
                    return
                try:
                    page_index = int(_first(parse_qs(parsed.query), "page") or "")
                    overlay = preparation.foundation.overlay(match.group(1), page_index)
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"page": overlay})
                return
            match = _REVISION_PDF.fullmatch(parsed.path)
            if match:
                if not self._authorized():
                    return
                try:
                    self._file(service.pdf_path(match.group(1)), "application/pdf", ranged=True)
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            match = _REVISION_COVER.fullmatch(parsed.path)
            if match:
                # Cover images load through <img>, which cannot send headers. Inside the
                # platform iframe the launch cookie is third-party and withheld, so the
                # launch token may arrive as a query parameter instead.
                supplied = parse_qs(urlparse(self.path).query).get("token", [])
                if not (len(supplied) == 1 and self._token_matches(supplied[0])):
                    if not self._authorized():
                        return
                try:
                    payload = service.cover_png(match.group(1))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass
                return
            if parsed.path.startswith("/vendor/"):
                vendor_target = {
                    "/vendor/pdf.mjs": pdfjs_root.joinpath("pdf.mjs"),
                    "/vendor/pdf.worker.mjs": pdfjs_root.joinpath("pdf.worker.mjs"),
                    "/vendor/marked.esm.js": marked_root.joinpath("marked.esm.js"),
                    "/vendor/purify.es.mjs": dompurify_root.joinpath("purify.es.mjs"),
                    "/vendor/katex.mjs": katex_root.joinpath("katex.mjs"),
                    "/vendor/katex.css": katex_root.joinpath("katex.css"),
                }.get(parsed.path)
                if parsed.path.startswith("/vendor/pdfjs/"):
                    # Expose only flat PDF.js asset directories from this installed
                    # version, including decoder JS fallbacks. Never arbitrary paths.
                    resource = re.fullmatch(
                        r"/vendor/pdfjs/(wasm|cmaps|standard_fonts|iccs)/([A-Za-z0-9][A-Za-z0-9_.-]*)",
                        parsed.path,
                    )
                    extensions = {
                        "wasm": {".wasm", ".js"},
                        "cmaps": {".bcmap"},
                        "standard_fonts": {".pfb", ".ttf"},
                        "iccs": {".icc"},
                    }
                    if resource:
                        folder, name = resource.groups()
                        resource_root = pdfjs_root.parent.joinpath(folder).resolve()
                        candidate = resource_root.joinpath(name).resolve()
                        if candidate.parent == resource_root and candidate.suffix in extensions[folder]:
                            vendor_target = candidate
                    if vendor_target is None:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "PDF.js resource not found"})
                        return
                if parsed.path.startswith("/vendor/fonts/"):
                    font_name = parsed.path.removeprefix("/vendor/fonts/")
                    if font_name and Path(font_name).name == font_name:
                        vendor_target = katex_root.joinpath("fonts", font_name)
                if vendor_target is None or not vendor_target.is_file():
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "Frontend dependency is unavailable; run npm install"},
                    )
                    return
                content_type = mimetypes.guess_type(vendor_target.name)[0]
                if vendor_target.suffix in (".js", ".mjs"):
                    content_type = "text/javascript; charset=utf-8"
                elif vendor_target.suffix == ".wasm":
                    content_type = "application/wasm"
                self._file(vendor_target, content_type or "application/octet-stream")
                return
            plugin_asset = _PLUGIN_ASSET.fullmatch(parsed.path)
            if plugin_asset:
                # 插件页与 Reader 同源，但不带 launch token：插件只读自己的静态资源，
                # 所有 /api/* 仍需 token，所以插件页拿不到任何后端能力（§3.4）。
                if not self._authorized():
                    return
                if plugins is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "插件服务不可用。"})
                    return
                try:
                    target, plugin_type = plugins.asset(
                        plugin_asset.group(1), unquote(plugin_asset.group(2))
                    )
                except PluginNotFound as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                # 插件静态资源按版本不变，允许短期缓存（词典的分块 JSON 重开不用重下）
                self._file(target, plugin_type, cache_control="private, max-age=3600")
                return
            self._static(parsed.path, static_root)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/notes":
                if not self._authorized() or not self._notes_ready():
                    return
                try:
                    payload = self._read_json()
                    unknown = set(payload) - {
                        "title", "body", "book_source_revision_id", "page_index", "pinned"
                    }
                    if unknown:
                        raise ValueError("笔记包含无法识别的字段。")
                    note = notes.create(
                        title=payload.get("title"),
                        body=payload.get("body"),
                        book_source_revision_id=payload.get("book_source_revision_id"),
                        page_index=payload.get("page_index"),
                        pinned=payload.get("pinned"),
                    )
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._json(HTTPStatus.CREATED, {"note": note})
                return
            if parsed.path == "/api/library/open-folder":
                # 在系统资源管理器中打开 PDF 实际存储文件夹（内容寻址 blobs 目录）。
                if not self._authorized():
                    return
                folder = service.paths.blob_root()
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    os.startfile(str(folder))  # noqa: S606 - Windows 资源管理器打开受管目录
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"无法打开 PDF 文件夹：{exc}"})
                    return
                self._json(HTTPStatus.OK, {"path": str(folder)})
                return
            region_match = _REVISION_VISUAL_REGION.fullmatch(parsed.path)
            if region_match:
                if not self._authorized():
                    return
                try:
                    payload = self._read_json()
                    if set(payload) != {"pdf_page_index", "rect"}:
                        raise ValueError("请选择当前 PDF 页中的一个区域。")
                    result = render_visual_region(
                        service, region_match.group(1), payload["pdf_page_index"], payload["rect"],
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_VISUAL_REGION", "error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"code": "VISUAL_SOURCE_NOT_FOUND", "error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            if self._reading_request(parsed.path, 'POST'):
                return
            if self._memory_request(parsed.path, 'POST'):
                return
            guide = re.fullmatch(r"/api/revisions/([0-9a-f-]+)/sections/([0-9a-f-]+)/guide/(generate|regenerate|retry)", parsed.path)
            if guide:
                if not self._authorized():
                    return
                if teaching is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "导读服务不可用。"})
                    return
                try:
                    payload = self._read_json()
                    revision, section, action = guide.groups()
                    result = teaching.retry(revision, section, payload["asset_id"]) if action == "retry" else teaching.request(revision, section, payload["intent_id"], regenerate=action == "regenerate")
                except (KeyError, LookupError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            save_match = _REVISION_ASSISTANT_SAVE.fullmatch(parsed.path)
            if save_match:
                if not self._authorized():
                    return
                if saved_explanations is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SAVE_EXPLANATION_UNAVAILABLE",
                        "error": "保存 Assistant 解释功能不可用。",
                    })
                    return
                try:
                    payload = self._read_json()
                    result = saved_explanations.save(
                        save_match.group(1),
                        reader_session_id=payload["reader_session_id"],
                        root_id=payload["root_id"],
                        node_id=payload.get("node_id"),
                        turn_id=payload["turn_id"],
                        save_intent_id=payload["save_intent_id"],
                        excerpt=payload.get("excerpt"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_SAVE_EXPLANATION", "error": str(exc)
                    })
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc)
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {
                        "code": "ASSISTANT_SAVE_TARGET_GONE", "error": str(exc)
                    })
                    return
                # AI 笔记（整段解释 / 选区摘录）同样要能在 PDF 页面上看到标记：
                # auto 模式下立即排队写回，manual 模式下由批注面板的「立即写回」处理。
                if result.get("created"):
                    pending = self._queue_annotation_pdf_sync(save_match.group(1))
                    if pending is not None:
                        result["pdf_sync"] = pending
                self._json(
                    HTTPStatus.CREATED if result["created"] else HTTPStatus.OK,
                    result,
                )
                return
            review_match = _REVISION_ANNOTATION_REVIEW.fullmatch(parsed.path)
            if review_match:
                if not self._authorized():
                    return
                if saved_explanations is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "REVIEW_UNAVAILABLE", "error": "AI 审查功能不可用。"
                    })
                    return
                try:
                    result = saved_explanations.retry_review(
                        review_match.group(1), review_match.group(2)
                    )
                except SavedExplanationError as exc:
                    self._json(HTTPStatus.CONFLICT, {
                        "code": exc.code, "error": exc.user_message
                    })
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.ACCEPTED, result)
                return
            bakeoff_match = _REVISION_ASSISTANT_BAKEOFF.fullmatch(parsed.path)
            if bakeoff_match:
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "AI_UNCONFIGURED", "error": "尚未配置 AI 功能；Reader 其余能力仍可使用。"
                    })
                    return
                try:
                    payload = self._read_json()
                    comparison = assistant.bake_off_selection(
                        payload["reader_session_id"],
                        bakeoff_match.group(1),
                        int(payload["pdf_page_index"]),
                        start=payload["start"],
                        end=payload["end"],
                        question=payload.get("question"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_BAKEOFF", "error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"code": "ASK_CONTEXT_NOT_FOUND", "error": str(exc)})
                    return
                except ProviderFailure as exc:
                    self._provider_failure(exc)
                    return
                self._json(HTTPStatus.OK, {"comparison": comparison})
                return
            ask_match = _REVISION_ASSISTANT_ASK.fullmatch(parsed.path)
            if ask_match:
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "AI_UNCONFIGURED", "error": "尚未配置 AI 功能；Reader 其余能力仍可使用。"
                    })
                    return
                streaming = False
                try:
                    payload = self._read_json()
                    skill_record = self._requested_skill(payload.get("skill"))
                    streaming = self._wants_assistant_stream()
                    if streaming:
                        self._start_assistant_stream()
                    if payload.get("source_kind") == "VISUAL_REGION":
                        state = assistant.ask_visual(
                            payload["reader_session_id"], ask_match.group(1), payload["pdf_page_index"],
                            rect=payload["rect"], question=payload.get("question"),
                            provider=payload.get("provider"),
                            stream=self._assistant_stream_event if streaming else None,
                            continuation=payload.get("continuation"),
                            skill_record=skill_record,
                        )
                    elif payload.get("source_kind") == "READING_GUIDE":
                        if teaching is None:
                            raise ValueError("导读服务不可用。")
                        context = teaching.selection_context(ask_match.group(1), payload["section_id"], payload["asset_id"], payload["module_id"], payload["start_offset"], payload["end_offset"])
                        state = assistant.ask_guide(
                            payload["reader_session_id"], ask_match.group(1), context,
                            payload.get("provider"), self._assistant_stream_event if streaming else None,
                            payload.get("continuation"), skill_record=skill_record,
                        )
                    else:
                        state = assistant.ask_selection(
                            payload["reader_session_id"],
                            ask_match.group(1),
                            int(payload["pdf_page_index"]),
                            start=payload["start"],
                            end=payload["end"],
                            provider=payload.get("provider"),
                            source_kind=payload.get("source_kind", "ORIGINAL_PDF"),
                            stream=self._assistant_stream_event if streaming else None,
                            continuation=payload.get("continuation"),
                            skill_record=skill_record,
                            question=payload.get("question"),
                        )
                except (KeyError, TypeError, ValueError) as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.BAD_REQUEST, "INVALID_ASK", str(exc)
                    )
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc, streaming=streaming)
                    return
                except LookupError as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.NOT_FOUND, "ASK_CONTEXT_NOT_FOUND", str(exc)
                    )
                    return
                except ProviderFailure as exc:
                    self._provider_failure(exc, streaming=streaming)
                    return
                except StreamConsumerDisconnected:
                    return
                if not streaming:
                    self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/child":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "AI_UNCONFIGURED", "error": "尚未配置 AI 功能；Reader 其余能力仍可使用。"
                    })
                    return
                streaming = False
                try:
                    payload = self._read_json()
                    streaming = self._wants_assistant_stream()
                    if streaming:
                        self._start_assistant_stream()
                    state = assistant.create_child(
                        payload["reader_session_id"],
                        payload["root_id"],
                        parent_node_id=payload.get("parent_node_id"),
                        turn_id=payload["turn_id"],
                        start_offset=int(payload["start_offset"]),
                        end_offset=int(payload["end_offset"]),
                        node_id=payload.get("node_id"),
                        source_spans=payload.get("source_spans"),
                        stream=self._assistant_stream_event if streaming else None,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.BAD_REQUEST, "INVALID_CHILD", str(exc)
                    )
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc, streaming=streaming)
                    return
                except LookupError as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.NOT_FOUND, "ASSISTANT_LEVEL_GONE", str(exc)
                    )
                    return
                except ProviderFailure as exc:
                    self._provider_failure(exc, streaming=streaming)
                    return
                except StreamConsumerDisconnected:
                    return
                if not streaming:
                    self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/retry-child":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "AI_UNCONFIGURED", "error": "尚未配置 AI 功能；Reader 其余能力仍可使用。"
                    })
                    return
                streaming = False
                try:
                    payload = self._read_json()
                    streaming = self._wants_assistant_stream()
                    if streaming:
                        self._start_assistant_stream()
                    state = assistant.retry_child(
                        payload["reader_session_id"], payload["root_id"], payload["node_id"],
                        stream=self._assistant_stream_event if streaming else None,
                        continuation=payload.get("continuation"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.BAD_REQUEST, "INVALID_CHILD", str(exc)
                    )
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc, streaming=streaming)
                    return
                except LookupError as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.NOT_FOUND, "ASSISTANT_LEVEL_GONE", str(exc)
                    )
                    return
                except ProviderFailure as exc:
                    self._provider_failure(exc, streaming=streaming)
                    return
                except StreamConsumerDisconnected:
                    return
                if not streaming:
                    self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/close-child":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "AI_UNCONFIGURED", "error": "AI 功能不可用"
                    })
                    return
                try:
                    payload = self._read_json()
                    state = assistant.close_child_subtree(
                        payload["reader_session_id"],
                        payload["root_id"],
                        payload["node_id"],
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_ASSISTANT_CHILD", "error": str(exc)
                    })
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc)
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {
                        "code": "ASSISTANT_LEVEL_GONE", "error": str(exc)
                    })
                    return
                self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/follow-up":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "AI_UNCONFIGURED", "error": "尚未配置 AI 功能；Reader 其余能力仍可使用。"
                    })
                    return
                streaming = False
                try:
                    payload = self._read_json()
                    streaming = self._wants_assistant_stream()
                    if streaming:
                        self._start_assistant_stream()
                    state = assistant.follow_up(
                        payload["reader_session_id"],
                        payload["root_id"],
                        payload["question"],
                        node_id=payload.get("node_id"),
                        stream=self._assistant_stream_event if streaming else None,
                        continuation=payload.get("continuation"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.BAD_REQUEST, "INVALID_FOLLOW_UP", str(exc)
                    )
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc, streaming=streaming)
                    return
                except LookupError as exc:
                    self._assistant_failure(
                        streaming, HTTPStatus.NOT_FOUND, "ASSISTANT_LEVEL_GONE", str(exc)
                    )
                    return
                except ProviderFailure as exc:
                    self._provider_failure(exc, streaming=streaming)
                    return
                except StreamConsumerDisconnected:
                    return
                if not streaming:
                    self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/focus":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"code": "AI_UNCONFIGURED", "error": "AI 功能不可用"})
                    return
                try:
                    payload = self._read_json()
                    state = assistant.focus(
                        payload["reader_session_id"],
                        payload["root_id"],
                        node_id=payload.get("node_id"),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_ASSISTANT_FOCUS", "error": str(exc)})
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc)
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"code": "ASSISTANT_LEVEL_GONE", "error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/close-root":
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"code": "AI_UNCONFIGURED", "error": "AI 功能不可用"})
                    return
                try:
                    payload = self._read_json()
                    state = assistant.close_root(
                        payload["reader_session_id"], payload["root_id"]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_ASSISTANT_ROOT", "error": str(exc)})
                    return
                except AssistantStateError as exc:
                    self._assistant_state_failure(exc)
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"code": "ASSISTANT_ROOT_GONE", "error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"assistant": state})
                return
            if parsed.path == "/api/assistant/close":
                if not self._authorized():
                    return
                try:
                    payload = self._read_json()
                    if assistant is not None:
                        assistant.close_session(payload["reader_session_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_SESSION", "error": str(exc)})
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            import_bookmarks = _REVISION_OUTLINE_IMPORT.fullmatch(parsed.path)
            if import_bookmarks:
                if not self._authorized():
                    return
                if outline is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Outline is unavailable"})
                    return
                try:
                    result = outline.import_pdf_bookmarks(import_bookmarks.group(1))
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, result)
                return
            sync_all = _REVISION_ANNOTATIONS_SYNC.fullmatch(parsed.path)
            if sync_all:
                if not self._authorized():
                    return
                if annotations is None or pdf_sync is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PDF_SYNC_UNAVAILABLE", "error": "PDF 写回不可用。"
                    })
                    return
                try:
                    report = self._sync_annotations_to_pdf(sync_all.group(1), sync_state["config"])
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "PDF_SYNC_FAILED", "error": f"写回 PDF 失败：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"pdf_sync": "synced", **report})
                return
            sync_one = _REVISION_ANNOTATION_SYNC.fullmatch(parsed.path)
            if sync_one:
                if not self._authorized():
                    return
                if annotations is None or pdf_sync is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PDF_SYNC_UNAVAILABLE", "error": "PDF 写回不可用。"
                    })
                    return
                try:
                    annotations.get(sync_one.group(1), sync_one.group(2))
                    report = self._sync_annotations_to_pdf(sync_one.group(1), sync_state["config"])
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "PDF_SYNC_FAILED", "error": f"写回 PDF 失败：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {
                    "pdf_sync": "synced",
                    "annotation_id": sync_one.group(2),
                    **report,
                })
                return
            annotation_match = _REVISION_ANNOTATIONS.fullmatch(parsed.path)
            if annotation_match:
                if not self._authorized():
                    return
                if annotations is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Annotations are unavailable"})
                    return
                try:
                    payload = self._read_json()
                    kind = payload.get("kind", "HIGHLIGHT")
                    if not isinstance(kind, str):
                        raise ValueError("Annotation kind is invalid")
                    kind = kind.strip().upper()
                    if kind == "AI_NOTE":
                        raise ValueError("AI 解释请走 Assistant 保存路径，不能在此创建。")
                    page_index = payload.get("pdf_page_index", payload.get("page_index"))
                    if not isinstance(page_index, int) or isinstance(page_index, bool):
                        raise ValueError("批注需要页码 page_index。")
                    annotation = annotations.create(
                        annotation_match.group(1),
                        kind=kind,
                        page_index=page_index,
                        start=payload.get("start"),
                        end=payload.get("end"),
                        rect=payload.get("rect"),
                        body=payload.get("body"),
                        highlight_style=(
                            payload.get("highlight_style")
                            or payload.get("color")
                            or "YELLOW"
                        ),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                result = {"annotation": annotation}
                pending = self._queue_annotation_pdf_sync(annotation_match.group(1))
                if pending is not None:
                    result["pdf_sync"] = pending
                self._json(HTTPStatus.CREATED, result)
                return
            retry_match = _REVISION_PREPARATION_RETRY.fullmatch(parsed.path)
            if retry_match:
                if not self._authorized():
                    return
                if preparation is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Preparation is unavailable"})
                    return
                try:
                    payload = self._read_json()
                    requeued = preparation.retry_page(
                        retry_match.group(1), int(payload["pdf_page_index"])
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(
                    HTTPStatus.ACCEPTED if requeued else HTTPStatus.CONFLICT,
                    {"status": "requeued" if requeued else "page is not failed"},
                )
                return
            prepare_match = _REVISION_PREPARATION.fullmatch(parsed.path)
            if prepare_match:
                if not self._authorized():
                    return
                if preparation is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Preparation is unavailable"})
                    return
                try:
                    payload = self._read_json()
                    current_page = int(payload.get("current_page", 0))
                    visible_pages = {int(value) for value in payload.get("visible_pages", [])}
                    preparation.schedule_revision(
                        prepare_match.group(1), visible_pages=visible_pages, current_page=current_page
                    )
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.ACCEPTED, {"status": "scheduled"})
                return
            if parsed.path == "/api/onboarding/complete":
                if not self._authorized():
                    return
                try:
                    reader_settings.set_onboarding_done(True)
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "ONBOARDING_NOT_SAVED", "error": f"引导状态未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"done": True})
                return
            if parsed.path == "/api/settings/library/migrate":
                # 显式迁移：把默认 blobs 里的旧文件搬进新库目录（同名跳过不覆盖）
                if not self._authorized():
                    return
                if not isinstance(service.paths, ManagedPaths):
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "LIBRARY_UNAVAILABLE", "error": "书库服务不可用。"
                    })
                    return
                try:
                    payload = self._read_json()
                    dry_run = bool(payload.get("dryRun")) if isinstance(payload, dict) else False
                    if dry_run:
                        status = _library_migrate_status(service.paths)
                        self._json(HTTPStatus.OK, {"dryRun": True, **status})
                    else:
                        result = _library_migrate_run(service.paths)
                        self._json(HTTPStatus.OK, {
                            "dryRun": False,
                            **result,
                            **_library_migrate_status(service.paths),
                        })
                except (ValueError, OSError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "LIBRARY_MIGRATE_FAILED", "error": str(exc)
                    })
                    return
                return
            if parsed.path == "/api/skills/install":
                if not self._authorized():
                    return
                if skills is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                try:
                    payload = self._read_json()
                    source_dir = payload.get("source_dir") or payload.get("path")
                    if not isinstance(source_dir, str) or not source_dir.strip():
                        raise ValueError("请提供技能包目录。")
                    record = SkillInstaller(skills.data_dir).install_from_directory(
                        Path(source_dir)
                    )
                except (KeyError, OSError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_SKILL_PACKAGE", "error": f"非法技能包：{exc}"
                    })
                    return
                self._json(HTTPStatus.CREATED, {"skill": {
                    "name": record.name,
                    "display_name": record.display_name,
                    "description": record.description,
                    "enabled": True,
                    "is_default": False,
                }})
                return
            if parsed.path == "/api/plugins/install":
                if not self._authorized():
                    return
                if plugins is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PLUGINS_UNAVAILABLE", "error": "插件服务不可用。"
                    })
                    return
                try:
                    record = plugins.install_zip(self._read_binary(MAX_PLUGIN_UPLOAD))
                except PluginError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_PLUGIN_PACKAGE", "error": f"非法插件包：{exc}"
                    })
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "PLUGIN_INSTALL_FAILED", "error": f"插件安装失败：{exc}"
                    })
                    return
                self._json(HTTPStatus.CREATED, {"plugin": record.to_json()})
                return
            restore_match = _SKILL_RESTORE.fullmatch(parsed.path)
            if restore_match:
                if not self._authorized():
                    return
                if skills is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                name = unquote(restore_match.group(1))
                try:
                    record = restore_skill(skills.data_dir, name, seed_source)
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "SKILL_RESTORE_FAILED", "error": f"恢复内置知识包失败：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {
                    "skill": self._skill_json(
                        record, {name}, library_mounts, skills.data_dir
                    )
                })
                return
            if parsed.path != "/api/books":
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
                return
            parameters = parse_qs(parsed.query)
            try:
                result = service.intake(
                    self.rfile,
                    content_length=length,
                    filename=unquote(self.headers.get("X-File-Name", "book.pdf")),
                    book_id=_first(parameters, "book_id"),
                    title=_first(parameters, "title"),
                    label=_first(parameters, "label"),
                )
            except IntakeError as exc:
                status = HTTPStatus.INSUFFICIENT_STORAGE if exc.code == "STORAGE_FULL" else HTTPStatus.UNPROCESSABLE_ENTITY
                self.close_connection = True
                self._json(status, {"code": exc.code, "error": str(exc)})
                return
            except LookupError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            if preparation is not None:
                preparation.schedule_revision(result["book"]["active_revision"]["id"])
            self._json(HTTPStatus.OK if result["duplicate"] else HTTPStatus.CREATED, result)

        def do_PUT(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/settings/library":
                # §5.3：只改路径不迁移——已有文件原地不动，之后导入的书才落新目录
                if not self._authorized():
                    return
                try:
                    payload = self._read_json()
                    path = payload.get("path") if isinstance(payload, dict) else None
                    resolved = reader_settings.set_library_root(path)
                    if isinstance(service.paths, ManagedPaths):
                        service.paths.set_blob_override(resolved)
                except (TypeError, ValueError, SettingsError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_LIBRARY_ROOT", "error": str(exc)
                    })
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "LIBRARY_NOT_SAVED", "error": f"书库目录未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, reader_settings.library_stats())
                return
            if parsed.path == "/api/settings/appearance":
                if not self._authorized():
                    return
                try:
                    payload = self._read_json()
                    theme = payload.get("theme")
                    if not isinstance(theme, str):
                        raise SettingsError("theme 必须是字符串。")
                    saved = reader_settings.set_theme(theme.strip().lower())
                except (TypeError, ValueError, SettingsError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_APPEARANCE", "error": str(exc)
                    })
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "SETTINGS_NOT_SAVED", "error": f"设置未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"theme": saved})
                return
            if parsed.path == "/api/settings/shortcuts":
                if not self._authorized():
                    return
                try:
                    payload = self._read_json()
                    shortcuts = payload.get("shortcuts") if isinstance(payload, dict) else None
                    if not isinstance(shortcuts, dict):
                        raise ValueError("shortcuts 必须是对象。")
                    unknown = set(shortcuts) - set(_SHORTCUT_ACTIONS)
                    if unknown:
                        raise ValueError("不支持的动作：" + "、".join(sorted(str(item) for item in unknown)))
                    clean: dict[str, str] = {}
                    for action, combo in shortcuts.items():
                        if not isinstance(combo, str):
                            raise ValueError(f"{action} 的快捷键必须是字符串。")
                        combo = combo.strip()
                        if combo:
                            clean[str(action)] = combo
                    (service.paths.root / _SHORTCUTS_FILENAME).write_text(
                        json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                    )
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"code": "SHORTCUTS_NOT_SAVED", "error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"shortcuts": clean})
                return
            if parsed.path == "/api/settings/annotation-sync":
                if not self._authorized():
                    return
                if pdf_sync is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PDF_SYNC_UNAVAILABLE", "error": "PDF 写回不可用。"
                    })
                    return
                current = sync_state["config"]
                try:
                    payload = self._read_json()
                    unknown = set(payload) - {"mode", "target"}
                    if unknown:
                        raise ValueError("不支持的设置项。")
                    for key in ("mode", "target"):
                        if key in payload and not isinstance(payload[key], str):
                            raise ValueError(f"{key} 必须是字符串。")
                    # Clients may send upper case ("AUTO", "SOURCE"); the module
                    # compares lower case, so normalise before storing in memory.
                    updated = AnnotationSyncConfig(
                        mode=payload.get("mode", current.mode).strip().lower(),
                        target=payload.get("target", current.target).strip().lower(),
                    )
                    updated.save(service.paths.root)
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "SETTINGS_NOT_SAVED", "error": f"设置未保存：{exc}"
                    })
                    return
                sync_state["config"] = updated
                self._json(HTTPStatus.OK, {"mode": updated.mode, "target": updated.target})
                return
            enabled_match = _SKILL_ENABLED.fullmatch(parsed.path)
            if enabled_match:
                if not self._authorized():
                    return
                if skills is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                name = unquote(enabled_match.group(1))
                if name == BUILTIN_SKILL_NAME:
                    self._json(HTTPStatus.CONFLICT, {
                        "code": "BUILTIN_SKILL_PROTECTED", "error": "内置技能不可禁用。"
                    })
                    return
                try:
                    payload = self._read_json()
                    enabled = payload["enabled"]
                    if not isinstance(enabled, bool):
                        raise ValueError("enabled 必须是布尔值。")
                    skills.set_enabled(name, enabled)
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "SKILL_CONFIG_NOT_SAVED", "error": f"技能设置未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"name": name, "enabled": enabled})
                return
            library_match = _SKILL_LIBRARY.fullmatch(parsed.path)
            if library_match:
                # 只登记路径，不复制文件（§6.5）
                if not self._authorized():
                    return
                if library_mounts is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                name = unquote(library_match.group(1))
                try:
                    payload = self._read_json()
                    path = payload.get("path")
                    if not isinstance(path, str) or not path.strip():
                        raise ValueError("请提供资料目录路径。")
                    status = library_mounts.mount(name, path)
                except (KeyError, TypeError, ValueError, LibraryMountError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {
                        "code": "INVALID_LIBRARY_PATH", "error": str(exc)
                    })
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "LIBRARY_NOT_SAVED", "error": f"资料库登记未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"name": name, "library": status})
                return
            plugin_enabled = _PLUGIN_ENABLED.fullmatch(parsed.path)
            if plugin_enabled:
                if not self._authorized():
                    return
                if plugins is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PLUGINS_UNAVAILABLE", "error": "插件服务不可用。"
                    })
                    return
                plugin_id = plugin_enabled.group(1)
                try:
                    payload = self._read_json()
                    enabled = payload["enabled"]
                    if not isinstance(enabled, bool):
                        raise ValueError("enabled 必须是布尔值。")
                    record = plugins.set_enabled(plugin_id, enabled)
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except PluginNotFound as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "PLUGIN_STATE_NOT_SAVED", "error": f"插件状态未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"plugin": record.to_json()})
                return
            provider_key_match = _PROVIDER_KEY.fullmatch(parsed.path)
            if provider_key_match:
                if not self._authorized():
                    return
                name = unquote(provider_key_match.group(1))
                if name not in PROVIDER_PRESETS:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "unknown provider"})
                    return
                try:
                    payload = self._read_json()
                    api_key = payload.get("api_key")
                    if not isinstance(api_key, str) or not api_key.strip():
                        raise ValueError("api_key 必须是非空字符串。")
                    write_provider_credential(name, api_key)
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "CREDENTIAL_NOT_SAVED", "error": f"密钥未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {
                    "name": name, "configured": True,
                    "credential_target": PROVIDER_PRESETS[name]["credential_target"],
                })
                return
            label_match = _REVISION_PAGE_LABEL.fullmatch(parsed.path)
            if label_match:
                if not self._authorized():
                    return
                if outline is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Page labels are unavailable"})
                    return
                try:
                    payload = self._read_json()
                    label = outline.set_manual_page_label(
                        label_match.group(1), int(label_match.group(2)), payload["printed_label"]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"page_label": label})
                return
            match = _REVISION_POSITION.fullmatch(parsed.path)
            if not match:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            try:
                payload = self._read_json()
                position = service.save_position(
                    match.group(1),
                    int(payload["pdf_page_index"]),
                    float(payload["normalized_offset"]),
                    float(payload["zoom"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except LookupError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"position": position})

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            note_item = _NOTE_ITEM.fullmatch(parsed.path)
            if note_item:
                if not self._authorized() or not self._notes_ready():
                    return
                try:
                    payload = self._read_json()
                    changes = {
                        key: payload[key]
                        for key in ("title", "body", "pinned", "book_source_revision_id", "page_index")
                        if key in payload
                    }
                    note = notes.update(note_item.group(1), **changes)
                except (TypeError, ValueError) as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"note": note})
                return
            annotation_match = _REVISION_ANNOTATION.fullmatch(parsed.path)
            if not annotation_match:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            if annotations is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Annotations are unavailable"})
                return
            try:
                payload = self._read_json()
                changes = {
                    key: payload[key] for key in ("body", "color", "rect") if key in payload
                }
                if not changes:
                    raise ValueError("没有可更新的批注字段。")
                annotation = annotations.update(
                    annotation_match.group(1), annotation_match.group(2), **changes
                )
            except (TypeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except LookupError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            result = {"annotation": annotation}
            pending = self._queue_annotation_pdf_sync(annotation_match.group(1))
            if pending is not None:
                result["pdf_sync"] = pending
            self._json(HTTPStatus.OK, result)

        def _reading_request(self, path, method):
            match = re.fullmatch(r'/api/revisions/([0-9a-f-]+)/section-reading', path)
            if not match:
                return False
            if not self._authorized():
                return True
            from reader_service.progress import reading
            try:
                service.revision(match[1])
                if method == 'GET':
                    result = reading.snapshot(service.database, match[1])
                else:
                    payload = self._read_json()
                    result = reading.observe(service.database, match[1], payload['pdf_page_index'],
                                             float(payload['top']), float(payload['bottom']))
            except (LookupError, KeyError, ValueError, TypeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {'error': str(exc)})
                return True
            self._json(HTTPStatus.OK, result)
            return True

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            note_item = _NOTE_ITEM.fullmatch(parsed.path)
            if note_item:
                if not self._authorized() or not self._notes_ready():
                    return
                try:
                    notes.delete(note_item.group(1))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            if self._memory_request(parsed.path, 'DELETE'):
                return
            annotation_match = _REVISION_ANNOTATION.fullmatch(parsed.path)
            if annotation_match:
                if not self._authorized():
                    return
                if annotations is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Annotations are unavailable"})
                    return
                try:
                    annotations.delete(annotation_match.group(1), annotation_match.group(2))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self._queue_annotation_pdf_sync(annotation_match.group(1))
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            history_item = _REVISION_ASSISTANT_HISTORY_ITEM.fullmatch(parsed.path)
            if history_item:
                if not self._authorized():
                    return
                if assistant is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Assistant is unavailable"})
                    return
                try:
                    assistant.delete_history(history_item.group(2))
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            library_match = _SKILL_LIBRARY.fullmatch(parsed.path)
            if library_match:
                if not self._authorized():
                    return
                if library_mounts is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                name = unquote(library_match.group(1))
                try:
                    status = library_mounts.unmount(name)
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "LIBRARY_NOT_SAVED", "error": f"资料库登记未保存：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"name": name, "library": status})
                return
            plugin_item = _PLUGIN_ITEM.fullmatch(parsed.path)
            if plugin_item:
                if not self._authorized():
                    return
                if plugins is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "PLUGINS_UNAVAILABLE", "error": "插件服务不可用。"
                    })
                    return
                try:
                    trashed = plugins.uninstall(plugin_item.group(1))
                except PluginNotFound as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except (OSError, ValueError) as exc:
                    self._json(HTTPStatus.CONFLICT, {
                        "code": "PLUGIN_UNINSTALL_FAILED", "error": f"插件卸载失败：{exc}"
                    })
                    return
                self._json(HTTPStatus.OK, {"trashed": str(trashed)})
                return
            skill_match = _SKILL.fullmatch(parsed.path)
            if skill_match:
                if not self._authorized():
                    return
                if skills is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "code": "SKILLS_UNAVAILABLE", "error": "技能服务不可用。"
                    })
                    return
                name = unquote(skill_match.group(1))
                if name == BUILTIN_SKILL_NAME:
                    self._json(HTTPStatus.CONFLICT, {
                        "code": "BUILTIN_SKILL_PROTECTED", "error": "内置技能不可卸载。"
                    })
                    return
                try:
                    SkillInstaller(skills.data_dir).uninstall(name)
                except LookupError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                    return
                except (OSError, ValueError) as exc:
                    self._json(HTTPStatus.CONFLICT, {
                        "code": "SKILL_UNINSTALL_FAILED", "error": f"技能卸载失败：{exc}"
                    })
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            provider_key_match = _PROVIDER_KEY.fullmatch(parsed.path)
            if provider_key_match:
                if not self._authorized():
                    return
                name = unquote(provider_key_match.group(1))
                if name not in PROVIDER_PRESETS:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "unknown provider"})
                    return
                try:
                    delete_provider_credential(name)
                except OSError as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "code": "CREDENTIAL_NOT_DELETED", "error": f"密钥删除失败：{exc}"
                    })
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            match = _BOOK.fullmatch(parsed.path)
            if not match:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            try:
                if preparation is not None:
                    preparation.cancel_book(match.group(1))
                service.delete_book(match.group(1))
            except LookupError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            except OSError as exc:
                self._json(
                    HTTPStatus.CONFLICT,
                    {"error": f"Delete did not complete and can be retried: {exc}"},
                )
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def _memory_request(self, path, method):
            match = re.fullmatch(r'/api/revisions/([0-9a-f-]+)/memory(?:/([0-9a-f-]+))?', path)
            if path != '/api/memory' and not match:
                return False
            if not self._authorized():
                return True
            try:
                if method == 'GET' and path == '/api/memory':
                    result = {'items': memory.list()}
                elif match and method == 'GET' and match[2]:
                    result = {'item': memory.get(match[1], match[2])}
                elif match and method == 'POST' and not match[2]:
                    payload = self._read_json()
                    if not isinstance(payload, dict) or set(payload) != {'source_kind', 'source_id'}:
                        raise ValueError('仅接受持久来源类型与标识。')
                    result = {'item': memory.collect(match[1], payload['source_kind'], payload['source_id'])}
                elif match and method == 'DELETE' and match[2]:
                    memory.remove(match[1], match[2])
                    result = {'removed': True}
                else:
                    self._json(HTTPStatus.METHOD_NOT_ALLOWED, {'error': '不支持此操作。'})
                    return True
                self._json(HTTPStatus.OK, result)
            except (ValueError, TypeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {'error': str(exc)})
            except LookupError as exc:
                self._json(HTTPStatus.NOT_FOUND, {'error': str(exc)})
            return True

        def _requested_skill(self, name):
            """Resolve the optional ``skill`` field of an Assistant ask request."""
            if name is None:
                return None
            if not isinstance(name, str) or not name.strip():
                raise ValueError("技能名称无效。")
            if skills is None:
                raise ValueError("技能服务不可用。")
            try:
                record = skills.get(name.strip())
            except LookupError:
                raise ValueError(f"技能不存在：{name.strip()}") from None
            if not record.enabled:
                raise ValueError(f"技能未启用：{record.name}")
            return record

        def _markdown_export(self, exporter, revision_id, scope, object_id, params):
            service.revision(revision_id)
            if scope == "assistant_conversation":
                if assistant is None or not object_id:
                    raise ValueError("请指定要导出的 Assistant 会话。")
                try:
                    root = assistant.public_root(object_id)
                except LookupError:
                    # 会话已随 Reader 关闭时，从本地归档里回退导出。
                    root = assistant.archived_root(object_id)
                content = exporter.export_assistant_conversation(revision_id, root)
                filename = exporter.build_filename(
                    revision_id,
                    page_index=(root.get("scope") or {}).get("pdf_page_index"),
                    topic=root.get("label"),
                )
                return content, filename
            if scope == "saved_explanation":
                if not object_id:
                    raise ValueError("请指定要导出的已保存解释。")
                content = exporter.export_saved_explanation(object_id)
                return content, exporter.build_filename(revision_id, topic="已保存解释")
            if scope == "annotations":
                content = exporter.export_annotations(revision_id)
                return content, exporter.build_filename(revision_id, topic="批注汇总")
            if scope == "bundle":
                roots = []
                if assistant is not None:
                    roots = [assistant.public_root(item) for item in params.get("root", [])]
                content = exporter.export_bundle(
                    revision_id,
                    assistant_roots=roots,
                    explanation_ids=params.get("explanation", []),
                    include_annotations=_first(params, "annotations") == "1",
                )
                return content, exporter.build_filename(revision_id, topic="学习导出")
            raise ValueError("导出范围无效。")

        def _markdown_download(self, content: str, filename: str) -> None:
            encoded = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            )
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

        def _markdown_deliver(self, content: str, filename: str, params) -> None:
            """save=1（桌面外壳）：由本地服务直接落盘，返回 JSON；
            否则按附件下发，交给浏览器 blob 下载。

            dialog=1 时弹系统「另存为」对话框让用户自选保存位置（用户的
            「下载」文件夹可能被移走/重定向）；取消返回 cancelled，对话框
            不可用或出错回落到「下载」文件夹。"""
            if _first(params, "save") != "1":
                self._markdown_download(content, filename)
                return
            if _first(params, "dialog") == "1":
                cancelled = False
                try:
                    chosen = ask_save_path(filename)
                    cancelled = chosen is None
                except DialogUnavailable:
                    chosen = None  # 环境不支持 → 回落「下载」文件夹
                except Exception:  # noqa: BLE001 - 对话框任何异常都走回落
                    chosen = None
                if chosen is not None:
                    try:
                        target = save_markdown_to_path(content, chosen)
                    except OSError as exc:
                        self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                        return
                    self._json(HTTPStatus.OK, {
                        "saved": True, "filename": target.name, "path": str(target),
                    })
                    return
                if cancelled:
                    # 对话框正常弹出但用户取消：不是错误，交给前端提示。
                    self._json(HTTPStatus.OK, {"saved": False, "cancelled": True})
                    return
            try:
                target = save_markdown_to_downloads(content, filename)
            except (OSError, ValueError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {
                "saved": True, "filename": target.name, "path": str(target),
            })

        def _queue_annotation_pdf_sync(self, revision_id: str) -> str | None:
            """Write annotations back in the background when the switch is ``auto``."""
            config = sync_state["config"]
            if pdf_sync is None or annotations is None or config.mode != "auto":
                return None
            threading.Thread(
                target=self._write_annotations_back,
                args=(revision_id, config),
                daemon=True,
            ).start()
            return "pending"

        def _write_annotations_back(self, revision_id: str, config) -> None:
            try:
                self._sync_annotations_to_pdf(revision_id, config)
            except Exception:
                # A failed write-back must never change the annotation result.
                logger.warning(
                    "批注写回 PDF 失败，已保留数据库结果：revision=%s target=%s",
                    revision_id,
                    config.target,
                    exc_info=True,
                )

        def _sync_annotations_to_pdf(self, revision_id: str, config) -> dict:
            return sync_annotations_to_pdf(
                service, annotations, pdf_sync, revision_id, config
            )

        def _notes_ready(self) -> bool:
            if notes is not None:
                return True
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "code": "NOTES_UNAVAILABLE", "error": "笔记服务不可用。"
            })
            return False

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-Reader-Token", "")
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                pass
            cookie_token = cookie.get(_SESSION_COOKIE)
            if self._token_matches(supplied) or (
                cookie_token is not None and self._token_matches(cookie_token.value)
            ):
                return True
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Missing or invalid launch token"})
            return False

        @staticmethod
        def _token_matches(supplied: str) -> bool:
            return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("Invalid JSON request size")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _read_binary(self, limit: int) -> bytes:
            """读原始请求体（插件 zip 上传用）；超限直接拒，不先落盘。"""
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise PluginError("请求体是空的。")
            if length > limit:
                raise PluginError("插件包超过 300MB 上限。")
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise PluginError("上传中断，插件包不完整。")
            return payload

        @staticmethod
        def _skill_json(record, seeded: set[str], library_mounts, data_dir=None) -> dict:
            """技能的一行状态：元信息 + 是否随包种子 + 体积 + 资料库挂载情况。"""
            root = None
            if record.references_dir is not None:
                root = record.references_dir.parent
            elif record.scripts_dir is not None:
                root = record.scripts_dir.parent
            elif data_dir is not None:
                root = Path(data_dir) / "skills" / record.name
            size = directory_size(root) if root is not None else 0
            payload = {
                "name": record.name,
                "display_name": record.display_name,
                "description": record.description,
                "enabled": record.enabled,
                "is_default": record.is_default,
                "seeded": record.name in seeded,
                "size": size,
                "size_human": human_size(size),
            }
            if library_mounts is not None and record.name in seeded:
                payload["library"] = library_mounts.status(record.name)
            return payload

        def _provider_failure(
            self, failure: ProviderFailure, *, streaming: bool = False
        ) -> None:
            payload = {
                "code": (
                    "AI_RESPONSE_LENGTH_LIMIT"
                    if failure.code == "response_length_limit"
                    else f"AI_{failure.kind.value}"
                ),
                "error": failure.user_message,
            }
            diagnostics = failure.diagnostics or {}
            metrics = {
                key: diagnostics.get(key)
                for key in ("ttft_ms", "latency_ms")
                if isinstance(diagnostics.get(key), int)
            }
            if metrics:
                payload["metrics"] = metrics
            if streaming:
                self._assistant_stream_event({"type": "error", **payload})
            else:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, payload)

        def _assistant_state_failure(
            self, failure: AssistantStateError, *, streaming: bool = False
        ) -> None:
            self._assistant_failure(
                streaming,
                HTTPStatus.CONFLICT,
                f"ASSISTANT_{failure.code}",
                failure.user_message,
            )

        def _assistant_failure(
            self, streaming: bool, status: HTTPStatus, code: str, message: str
        ) -> None:
            payload = {"code": code, "error": message}
            if streaming:
                self._assistant_stream_event({"type": "error", **payload})
            else:
                self._json(status, payload)

        def _wants_assistant_stream(self) -> bool:
            return "text/event-stream" in self.headers.get("Accept", "").casefold()

        def _start_assistant_stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self._assistant_stream_event({"type": "stage", "stage": "preparing"})

        def _assistant_stream_event(self, payload: dict) -> None:
            self._project_assistant_view(payload)
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            try:
                self.wfile.write(f"data: {encoded}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
                raise StreamConsumerDisconnected() from exc

        def _start_guide_stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()

        def _guide_stream_event(self, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            try:
                self.wfile.write(f"data: {encoded}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
                raise StreamConsumerDisconnected() from exc

        # 静态资源按「目录顶层 + 允许的后缀 + 文件确实存在」放行，
        # 不再维护文件名白名单：前端新增模块（如 assistant-history-ui.js）
        # 不必同步改服务端，避免出现 404 打断 app.js 的模块图。
        static_extensions = frozenset(
            {
                ".js", ".mjs", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg",
                ".webp", ".ico", ".woff", ".woff2", ".ttf", ".json", ".map", ".txt",
            }
        )

        def _static(self, path: str, directory: Path) -> None:
            filename = {"/": "index.html", "/index.html": "index.html"}.get(path)
            if filename is None and path.startswith("/"):
                candidate = path[1:]
                if (
                    candidate
                    and "/" not in candidate
                    and "\\" not in candidate
                    and ".." not in candidate
                    and Path(candidate).suffix.lower() in self.static_extensions
                    and directory.joinpath(candidate).is_file()
                ):
                    filename = candidate
            if filename is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            target = directory.joinpath(filename)
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix in (".js", ".mjs"):
                content_type = "text/javascript; charset=utf-8"
            headers = None
            if filename in {"index.html", "notes.html"}:
                # 独立小窗（如笔记）由 Rust 以 ``?token=`` 打开，与 Reader 主页同样
                # 用查询参数换取 launch cookie；随后页面跳转/刷新都靠 cookie 鉴权。
                supplied = parse_qs(urlparse(self.path).query).get("token", [])
                if not (len(supplied) == 1 and self._token_matches(supplied[0])):
                    if not self._authorized():
                        return
                headers = {
                    "Set-Cookie": (
                        f"{_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"
                    ),
                    "Referrer-Policy": "no-referrer",
                }
                self._html(target, headers=headers)
                return
            self._file(target, content_type, headers=headers)

        def _html(self, path: Path, headers: dict[str, str] | None = None) -> None:
            """把当前主题偏好注进 <html>，让首屏在样式表落地前就是对的主题色。

            主题偏好存在 settings.json 里，前端拿不到之前只能靠 localStorage 猜；
            服务端直接写进标签属性，用户选了深色就一次白屏都不会看到。
            「跟随系统」不注入 data-theme —— 由页面内联脚本按 matchMedia 判断。
            """
            text = path.read_text(encoding="utf-8")
            pref = reader_settings.theme()
            attr = f' data-theme-pref="{pref}"'
            if pref in ("light", "dark"):
                attr += f' data-theme="{pref}"'
            text = re.sub(r"<html\b([^>]*)>", lambda m: f"<html{m.group(1)}{attr}>", text, count=1)
            body = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

        def _file(
            self,
            path: Path,
            content_type: str,
            ranged: bool = False,
            headers: dict[str, str] | None = None,
            cache_control: str = "no-store",
        ) -> None:
            size = path.stat().st_size
            start, end = 0, size - 1
            status = HTTPStatus.OK
            if ranged and self.headers.get("Range"):
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"])
                if not match:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else end
                elif match.group(2):
                    count = int(match.group(2))
                    start = max(0, size - count)
                if start > end or start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = HTTPStatus.PARTIAL_CONTENT
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", cache_control)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if ranged:
                self.send_header("Accept-Ranges", "bytes")
                if status == HTTPStatus.PARTIAL_CONTENT:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = source.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                        break
                    remaining -= len(chunk)

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            # Browser tree navigation needs summaries plus the focused conversation only.
            # Full service state stays in memory; a focus request lazily restores its turns.
            self._project_assistant_view(payload)
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

        def _project_assistant_view(self, payload: dict) -> None:
            if self.headers.get("X-Assistant-View") != "current":
                return
            assistant_view = payload.get("assistant")
            if not isinstance(assistant_view, dict):
                return
            for root in assistant_view.get("roots", []):
                root.pop("turns", None)
                for node in root.get("nodes", []):
                    node.pop("turns", None)

        def _preparation_events(
            self, coordinator: PreparationCoordinator, revision_id: str
        ) -> None:
            try:
                coordinator.foundation.ensure_revision(revision_id)
            except LookupError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(b"retry: 1000\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return
            previous = None
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    pages = coordinator.foundation.statuses(revision_id)
                except LookupError:
                    # The book may be deleted while an already-open SSE stream winds down.
                    return
                terminal = bool(pages) and all(page["status"] in ("READY", "FAILED") for page in pages)
                active_jobs = False
                if terminal:
                    counts = coordinator.jobs.counts(revision_id)
                    active_jobs = bool(counts["QUEUED"] or counts["RUNNING"])
                signature = tuple(
                    (page["pdf_page_index"], page["status"], page["prepared_at"], page["failure_code"])
                    for page in pages
                ) + (active_jobs,)
                if signature != previous:
                    payload = json.dumps(
                        {"pages": pages, "active_jobs": active_jobs},
                        ensure_ascii=False, separators=(",", ":"),
                    )
                    try:
                        self.wfile.write(f"event: pages\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                        return
                    previous = signature
                if terminal and not active_jobs:
                    return
                time.sleep(0.25)

        def log_message(self, format: str, *args: object) -> None:
            # Deliberately omit URLs so the per-launch token never enters logs.
            print(f"{self.client_address[0]} {args[1] if len(args) > 1 else '-'}")

    return Handler


def provider_catalog() -> list[dict]:
    """Registry presets plus whether a credential is already available."""
    catalog = []
    for name, preset in PROVIDER_PRESETS.items():
        try:
            configured = read_provider_credential(name).available
        except (OSError, ValueError):
            configured = False
        catalog.append({
            "name": name,
            "display_name": preset["display_name"],
            "endpoint": preset["endpoint"],
            "default_model": preset["default_model"],
            "credential_target": preset["credential_target"],
            "configured": configured,
        })
    return catalog


def sync_annotations_to_pdf(
    service: LibraryService,
    annotations: AnnotationService,
    pdf_sync: PdfAnnotationSync,
    revision_id: str,
    config: AnnotationSyncConfig,
) -> dict:
    """Write durable annotations back into the PDF (source, or an annotated copy)."""
    pdf_path = service.pdf_path(revision_id)
    values = annotations.list_all(revision_id)
    if config.target == "copy":
        target_dir = service.paths.resolve("annotated")
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / f"{pdf_path.stem}-annotated.pdf"
        report = pdf_sync.export_annotated_copy(pdf_path, values, destination)
    else:
        pdf_sync.backup_pdf(pdf_path, service.paths.resolve("annotation-backups"))
        report = pdf_sync.write_annotations(pdf_path, values)
        destination = pdf_path
    return {
        "target": config.target,
        "annotations": len(values),
        "written": report.written,
        "removed": report.removed,
        "skipped": report.skipped,
        "path": str(destination),
    }


def _first(parameters: dict[str, list[str]], key: str) -> str | None:
    values = parameters.get(key)
    return values[0] if values else None
