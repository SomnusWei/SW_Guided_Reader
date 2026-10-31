from __future__ import annotations

import sys


def _force_utf8_streams() -> None:
    """标准输出统一为 UTF-8（Windows 管道默认 GBK，会让父进程解码失败）。"""
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            continue


_force_utf8_streams()

# 启动自检：本模块顶部会 import 依赖 pypdf/pypdfium2 的子模块，
# 用错误解释器（如系统 Python）启动时会直接 ImportError。
# 这里提前拦截，给出可操作的中文提示，而不是裸 traceback。
for _module in ("pypdf", "pypdfium2"):
    try:
        __import__(_module)
    except ImportError:
        print(
            f"启动失败：缺少 PDF 解析依赖 {_module}。\n"
            "当前解释器可能不是项目虚拟环境（.venv）。\n"
            "请改用 .venv\\Scripts\\python.exe -m reader_service 启动；\n"
            "若尚未安装依赖，先执行：python -m pip install -e .",
            file=sys.stderr,
        )
        raise SystemExit(2)

import argparse
import atexit
import json
import logging
import os
import secrets
import signal
import threading
import time
from urllib.parse import urlencode
import webbrowser
from pathlib import Path

from reader_service.agent_runtime import ProviderRuntimeSet
from reader_service.annotation import (
    AnnotationRepository,
    AnnotationService,
    AnnotationSyncConfig,
    PdfAnnotationSync,
)
from reader_service.assistant import AssistantContextBuilder, AssistantHistoryService, AssistantService
from reader_service.export_md import MarkdownExporter
from reader_service.library import LibraryService
from reader_service.foundation import (
    FoundationRepository,
    FoundationService,
    PageLabelRepository,
    PageLabelService,
)
from reader_service.foundation.umi_ocr import UmiOcrEngine
from reader_service.jobs import JobRepository, PreparationCoordinator
from reader_service.outline import OutlineRepository, OutlineService
from reader_service.server import ReaderServer, handler_factory
from reader_service.notes import NotesService
from reader_service.saved_explanations import SavedExplanationService
from reader_service.plugins import PluginRegistry
from reader_service.skills import SkillRegistry, seed_default_skills
from reader_service.settings_store import ReaderSettings
from reader_service.storage import ManagedPaths
from reader_service.teaching.service import TeachingService

logger = logging.getLogger(__name__)

LAUNCH_FILENAME = "reader-launch.json"
# 2.0：技能种子是「整目录」，不再是单个技能目录
DEFAULT_SKILLS_DIR_ENV = "GUIDED_READER_DEFAULT_SKILLS_DIR"


def default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data, "408 Guided Reader")
    return Path.cwd().joinpath("var", "reader-data")


def default_prepare_workers() -> int:
    # Scanned books need every page OCR'd. On larger CPUs, a second page
    # worker reduces the full-book wait while leaving cores for the Reader UI.
    return 2 if (os.cpu_count() or 1) >= 12 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the 408 Guided Reader")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--no-open", action="store_true", help="Do not launch the browser")
    parser.add_argument("--token", help=argparse.SUPPRESS)
    parser.add_argument("--prepare-workers", type=int, default=default_prepare_workers())
    parser.add_argument("--render-dpi", type=int, default=200)
    return parser


def write_launch_file(path: Path, url: str) -> None:
    payload = {"url": url, "pid": os.getpid(), "ts": time.time()}
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def remove_launch_file(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _interrupt(signum, frame) -> None:
    """Turn a termination signal into the same orderly shutdown as Ctrl+C."""
    raise KeyboardInterrupt


def install_stop_handlers() -> None:
    for name in ("SIGTERM", "SIGBREAK"):
        handler = getattr(signal, name, None)
        if handler is not None:
            signal.signal(handler, _interrupt)


def main() -> None:
    args = build_parser().parse_args()
    token = args.token or secrets.token_urlsafe(32)
    paths = ManagedPaths(args.data_dir)
    # §5.3：settings.json 里配置过外部书库 → 新导入落那里，旧书仍从默认 blobs 读
    reader_settings = ReaderSettings(args.data_dir)
    if reader_settings.library_root() != reader_settings.default_library_root():
        try:
            paths.set_blob_override(reader_settings.library_root())
            logger.info("书库目录：%s", paths.blob_override())
        except OSError as exc:
            logger.warning("外部书库目录不可用（%s），退回默认位置。", exc)
    service = LibraryService(paths)
    foundation = FoundationService(
        service,
        FoundationRepository(service.database),
        UmiOcrEngine,
        render_dpi=args.render_dpi,
    )
    page_labels = PageLabelService(service, PageLabelRepository(service.database))
    outline = OutlineService(service, OutlineRepository(service.database), page_labels)
    annotations = AnnotationService(foundation, AnnotationRepository(service.database))
    agent_runtime = ProviderRuntimeSet.from_environment()
    teaching = TeachingService(service.database, agent_runtime)
    preparation = PreparationCoordinator(
        service,
        foundation,
        JobRepository(service.database),
        worker_count=args.prepare_workers,
        outline=outline,
        teaching=teaching,
    )
    assistant = AssistantService(
        AssistantContextBuilder(foundation, outline),
        agent_runtime,
        history=AssistantHistoryService(service.database),
    )
    saved_explanations = SavedExplanationService(
        assistant,
        annotations,
        agent_runtime,
    )
    preparation.start()
    seed_source = os.environ.get(DEFAULT_SKILLS_DIR_ENV)
    seed_result = seed_default_skills(args.data_dir, seed_source)
    if seed_result.unavailable:
        logger.info("未找到随包技能种子（default-skills），AI 退回通用解释。")
    elif seed_result.installed or seed_result.filled:
        logger.info(
            "技能种子就绪：新装 %s｜补齐 %s",
            "、".join(seed_result.installed) or "无",
            "、".join(seed_result.filled) or "无",
        )
    skills = SkillRegistry(args.data_dir)
    plugins = PluginRegistry(args.data_dir)
    annotation_sync = AnnotationSyncConfig.load(args.data_dir)
    server = ReaderServer(
        (args.host, args.port),
        handler_factory(
            service,
            token,
            preparation=preparation,
            annotations=annotations,
            outline=outline,
            assistant=assistant,
            saved_explanations=saved_explanations,
            teaching=teaching,
            annotation_sync=annotation_sync,
            pdf_sync=PdfAnnotationSync(),
            skills=skills,
            exporter=MarkdownExporter(service.database, service.paths),
            notes=NotesService(service.database),
            plugins=plugins,
            seed_source=seed_source,
        ),
    )
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/?{urlencode({'token': token})}"
    print(f"READY {url}", flush=True)
    print(
        "Keep this terminal open while reading. Copy the READY URL into Chrome or Edge if no browser opens.",
        flush=True,
    )
    launch_file = service.paths.root.joinpath(LAUNCH_FILENAME)
    write_launch_file(launch_file, url)
    atexit.register(remove_launch_file, launch_file)
    install_stop_handlers()
    if not args.no_open:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        remove_launch_file(launch_file)
        server.server_close()
        preparation.stop()


if __name__ == "__main__":
    main()
