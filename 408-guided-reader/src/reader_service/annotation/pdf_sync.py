"""Sync durable Reader annotations into the original PDF as standard PDF comments.

The module owns three jobs:

1. ``write_annotations`` — full sync: remove every Reader-marked annotation,
   write the current annotation set, and replace the source PDF atomically.
2. ``export_annotated_copy`` — write the same result to a copy, never touching
   the source file.
3. ``read_reader_annotations`` — read Reader-marked annotations back into
   annotation-shaped dicts so a PDF that already carries them can re-link.

Reader annotations are marked with the ``/NM`` name field using the prefix
``guided-reader-`` followed by the durable annotation id. Annotations from
other tools are never removed or modified.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText, Highlight, Popup, Text
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    RectangleObject,
    TextStringObject,
)

READER_MARKER_PREFIX = "guided-reader-"
REGION_OPACITY = 0.3
SUPPORTED_ROTATIONS = frozenset({0, 90, 180, 270})

# Reader color names to PDF annotation hex colors (no leading '#').
COLOR_HEX = {
    "YELLOW": "ffff00",
    "GREEN": "00ff00",
    "BLUE": "0000ff",
    "RED": "ff0000",
    "PURPLE": "9932cc",
}
_HEX_TO_COLOR = {value: key for key, value in COLOR_HEX.items()}


def markdown_to_plain_text(markdown: str) -> str:
    """PDF 便签注释是纯文本：剥除 markdown 语法符号，保留可读内容。

    应用内的 AI 笔记卡片会按 markdown 渲染，但写进 PDF 的注释没有排版能力，
    原样写入会显示 ``**加粗**``、``| 表格 |`` 这类原始符号（真机反馈）。
    这里做保守剥除：去标记、不删词，代码块内容原样保留。
    """
    text = str(markdown or "")
    # 围栏代码：去掉围栏行，保留内容
    text = re.sub(r"```[^\n]*\n?", "", text)
    # 行内代码、加粗、斜体、删除线标记
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(\*\*\*|___)(?=\S)(.+?)(?<=\S)\1", r"\2", text, flags=re.S)
    text = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", text, flags=re.S)
    text = re.sub(r"(\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"\1", text)
    # 链接/图片：保留文字，丢 URL
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # 标题、引用、无序列表标记
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)
    text = re.sub(r"(?m)^\s{0,3}[-*+]\s+", "· ", text)
    # 表格：分隔行（|---|---|）整行删除；其余行的竖线换成全角空格
    text = re.sub(r"(?m)^\s*\|(\s*:?-{2,}:?\s*\|)+\s*$", "", text)
    text = text.replace("|", "　")
    # 数学定界符
    text = text.replace("$$", "")
    return text.strip()


def _resolve_color(value: str | None) -> str | None:
    """Reader 颜色名或 #RRGGBB → PDF 注解用的六位 hex（无 #）；无法识别返回 None。"""
    if not value:
        return None
    if value.startswith("#") and len(value) == 7:
        try:
            int(value[1:], 16)
        except ValueError:
            return None
        return value[1:].lower()
    return COLOR_HEX.get(value.upper())

SUBTYPE_TO_KIND = {
    "/Highlight": "HIGHLIGHT",
    "/Underline": "UNDERLINE",
    "/FreeText": "TEXTBOX",
    "/Square": "REGION",
    "/Text": "AI_NOTE",
}

_SYNC_MODES = frozenset({"off", "manual", "auto"})
_SYNC_TARGETS = frozenset({"source", "copy"})
CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class AnnotationSyncConfig:
    """Where and when the server may write annotations back into PDF files."""

    mode: str = "manual"
    target: str = "source"

    @classmethod
    def from_environment(cls, environ=None) -> "AnnotationSyncConfig":
        import os

        env = os.environ if environ is None else environ
        return cls(
            mode=_clean_mode(env.get("GUIDED_READER_ANNOTATION_PDF_SYNC"), "manual"),
            target=_clean_target(env.get("GUIDED_READER_ANNOTATION_PDF_TARGET"), "source"),
        )

    @classmethod
    def load(cls, data_dir=None, environ=None) -> "AnnotationSyncConfig":
        """Environment wins over the data-dir ``config.json``; defaults come last.

        Load-time values are normalized leniently (case-insensitive; invalid
        stored values fall back to defaults) so a hand-edited config.json can
        never break service startup. Strict validation still applies on save.
        """
        import os

        env = os.environ if environ is None else environ
        stored = _read_config_file(data_dir)
        return cls(
            mode=_clean_mode_lenient(
                env.get("GUIDED_READER_ANNOTATION_PDF_SYNC"),
                _clean_mode_lenient(stored.get("annotation_pdf_sync"), "manual"),
            ),
            target=_clean_target_lenient(
                env.get("GUIDED_READER_ANNOTATION_PDF_TARGET"),
                _clean_target_lenient(stored.get("annotation_pdf_target"), "source"),
            ),
        )

    def save(self, data_dir) -> None:
        """Persist the switch in the data directory without touching anything else."""
        path = Path(data_dir) / CONFIG_FILENAME
        stored = _read_config_file(data_dir)
        stored["annotation_pdf_sync"] = _clean_mode(self.mode, "manual")
        stored["annotation_pdf_target"] = _clean_target(self.target, "source")
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)


def _clean_mode_lenient(value, fallback: str) -> str:
    candidate = (value or "").strip().lower()
    return candidate if candidate in _SYNC_MODES else fallback


def _clean_target_lenient(value, fallback: str) -> str:
    candidate = (value or "").strip().lower()
    return candidate if candidate in _SYNC_TARGETS else fallback


def _clean_mode(value, fallback: str) -> str:
    candidate = (value or fallback).strip().lower()
    if candidate not in _SYNC_MODES:
        raise ValueError(
            "Annotation PDF sync mode must be one of: " + ", ".join(sorted(_SYNC_MODES))
        )
    return candidate


def _clean_target(value, fallback: str) -> str:
    candidate = (value or fallback).strip().lower()
    if candidate not in _SYNC_TARGETS:
        raise ValueError(
            "Annotation PDF sync target must be one of: " + ", ".join(sorted(_SYNC_TARGETS))
        )
    return candidate


def _read_config_file(data_dir) -> dict:
    if data_dir is None:
        return {}
    path = Path(data_dir) / CONFIG_FILENAME
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class SyncReport:
    written: int = 0
    removed: int = 0
    skipped: list = field(default_factory=list)


class PdfAnnotationSync:
    """Thread-safe bridge between durable annotations and PDF comment objects."""

    def __init__(self):
        self._lock = threading.RLock()

    # -- public API ---------------------------------------------------------

    def write_annotations(self, pdf_path, annotations) -> SyncReport:
        """Replace Reader-marked comments in the source PDF with ``annotations``."""
        with self._lock:
            pdf_path = Path(pdf_path)
            temporary = pdf_path.with_name(f"{pdf_path.name}.tmp-{os.getpid()}")
            try:
                report = self._render_annotations(pdf_path, annotations, temporary)
                os.replace(temporary, pdf_path)
                return report
            finally:
                if temporary.exists():
                    temporary.unlink()

    def export_annotated_copy(self, pdf_path, annotations, out_path) -> SyncReport:
        """Write the annotation set to ``out_path`` without touching the source."""
        with self._lock:
            pdf_path = Path(pdf_path)
            out_path = Path(out_path)
            if out_path.resolve() == pdf_path.resolve():
                raise ValueError("Export target must not overwrite the source PDF")
            temporary = out_path.with_name(f"{out_path.name}.tmp-{os.getpid()}")
            try:
                report = self._render_annotations(pdf_path, annotations, temporary)
                os.replace(temporary, out_path)
                return report
            finally:
                if temporary.exists():
                    temporary.unlink()

    def backup_pdf(self, pdf_path, backup_dir, keep: int = 3) -> Path:
        """Copy the current PDF into ``backup_dir`` and keep the newest ``keep`` backups."""
        if keep < 1:
            raise ValueError("Backup rotation must keep at least one file")
        with self._lock:
            pdf_path = Path(pdf_path)
            backup_dir = Path(backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
            destination = backup_dir / f"{pdf_path.stem}-{stamp}.pdf.bak"
            shutil.copy2(pdf_path, destination)
            backups = sorted(backup_dir.glob(f"{pdf_path.stem}-*.pdf.bak"))
            for stale in backups[:-keep]:
                stale.unlink()
            return destination

    def read_reader_annotations(self, pdf_path) -> list[dict]:
        """Restore Reader-marked PDF comments into annotation-shaped dicts."""
        with self._lock:
            reader = PdfReader(str(pdf_path))
            results = []
            for page_index, page in enumerate(reader.pages):
                annots = page.get("/Annots")
                if not annots:
                    continue
                width, height, rotation = self._page_geometry(page)
                for reference in annots:
                    annot = reference.get_object()
                    marker = annot.get("/NM")
                    if marker is None or not str(marker).startswith(READER_MARKER_PREFIX):
                        continue
                    restored = self._restore_annotation(
                        annot, page_index, width, height, rotation
                    )
                    if restored is not None:
                        results.append(restored)
            return results

    # -- writing ------------------------------------------------------------

    def _render_annotations(self, pdf_path: Path, annotations, out_path: Path) -> SyncReport:
        writer = PdfWriter(clone_from=str(pdf_path))
        report = SyncReport(removed=self._remove_reader_annotations(writer))
        page_count = len(writer.pages)
        for annotation in annotations:
            page_index = annotation.get("pdf_page_index")
            if not isinstance(page_index, int) or not 0 <= page_index < page_count:
                report.skipped.append({"id": annotation.get("id"), "reason": "PAGE_OUT_OF_RANGE"})
                continue
            width, height, rotation = self._page_geometry(writer.pages[page_index])
            built = self._build_annotation(annotation, width, height, rotation, writer)
            if built is None:
                report.skipped.append({"id": annotation.get("id"), "reason": "NOT_RENDERABLE"})
                continue
            try:
                self._add_with_popup(writer, page_index, annotation, built)
            except Exception:
                report.skipped.append({"id": annotation.get("id"), "reason": "WRITE_FAILED"})
                continue
            report.written += 1
        with open(out_path, "wb") as stream:
            writer.write(stream)
        writer.close()
        return report

    def _add_with_popup(self, writer, page_index, annotation, built) -> None:
        added = writer.add_annotation(page_number=page_index, annotation=built["annot"])
        popup_rect = built.get("popup_rect")
        if popup_rect is not None:
            writer.add_annotation(
                page_number=page_index,
                annotation=Popup(rect=popup_rect, parent=added),
            )

    @staticmethod
    def _remove_reader_annotations(writer) -> int:
        removed = 0
        for page in writer.pages:
            annots = page.get("/Annots")
            if not annots:
                continue
            annots = annots.get_object() if hasattr(annots, "get_object") else annots
            entries = list(annots)
            present_ids = {getattr(reference, "idnum", None) for reference in entries}
            doomed_ids = set()
            for reference in entries:
                marker = reference.get_object().get("/NM")
                if marker is not None and str(marker).startswith(READER_MARKER_PREFIX):
                    doomed_ids.add(getattr(reference, "idnum", None))
            kept = []
            for reference in entries:
                annot = reference.get_object()
                marker = annot.get("/NM")
                if marker is not None and str(marker).startswith(READER_MARKER_PREFIX):
                    removed += 1
                    continue
                # 便签/文本框的弹出窗是独立的 /Popup 对象、不带 /NM，父注释被删后
                # 会剩成没有归属的孤儿空框。每轮全量重写都漏掉它就累积一份，
                # 所以父注释被删或父引用已不在本页时一并清除。
                if str(annot.get("/Subtype", "")) == "/Popup":
                    parent_id = getattr(annot.get("/Parent"), "idnum", None)
                    if parent_id is not None and (
                        parent_id in doomed_ids or parent_id not in present_ids
                    ):
                        continue
                kept.append(reference)
            if len(kept) != len(entries):
                if kept:
                    page[NameObject("/Annots")] = ArrayObject(kept)
                else:
                    del page[NameObject("/Annots")]
        return removed

    def _build_annotation(
        self, annotation: dict, width: float, height: float, rotation: int, writer=None
    ):
        if rotation not in SUPPORTED_ROTATIONS:
            return None
        marker = f"{READER_MARKER_PREFIX}{annotation['id']}"
        kind = annotation.get("kind")
        color = _resolve_color(annotation.get("highlight_style"))
        if kind in ("HIGHLIGHT", "UNDERLINE", "AI_NOTE"):
            quads = annotation.get("quads")
            if not quads:
                return None
            quad_points, rect = self._quads_to_pdf(quads, width, height, rotation)
        else:
            rect_value = annotation.get("rect")
            if not rect_value:
                return None
            quad_points = None
            rect = self._display_rect_to_pdf(rect_value, width, height, rotation)
        if quad_points is None and rect is None:
            return None

        if kind == "HIGHLIGHT":
            annot = Highlight(
                rect=rect,
                quad_points=quad_points,
                highlight_color=color or COLOR_HEX["YELLOW"],
            )
            return {"annot": self._mark(annot, marker)}

        if kind == "UNDERLINE":
            annot = DictionaryObject()
            annot[NameObject("/Subtype")] = NameObject("/Underline")
            annot[NameObject("/Rect")] = RectangleObject(rect)
            annot[NameObject("/QuadPoints")] = quad_points
            annot[NameObject("/C")] = ArrayObject(
                [FloatObject(value) for value in _hex_to_rgb(color or COLOR_HEX["YELLOW"])]
            )
            return {"annot": self._mark(annot, marker)}

        if kind == "TEXTBOX":
            annot = FreeText(
                text=annotation.get("body") or "",
                rect=rect,
                background_color=None,
                border_color=color or COLOR_HEX["YELLOW"],
            )
            return {"annot": self._mark(annot, marker)}

        if kind == "REGION":
            annot = DictionaryObject()
            annot[NameObject("/Subtype")] = NameObject("/Square")
            annot[NameObject("/Rect")] = RectangleObject(rect)
            if color:
                annot[NameObject("/IC")] = ArrayObject(
                    [FloatObject(value) for value in _hex_to_rgb(color)]
                )
            annot[NameObject("/CA")] = FloatObject(REGION_OPACITY)
            return {"annot": self._mark(annot, marker)}

        if kind == "AI_NOTE":
            payload = annotation.get("payload") or {}
            question = str(payload.get("question") or "").strip()
            answer = str(payload.get("answer") or "").strip()
            excerpt = str(payload.get("excerpt") or "").strip()
            if excerpt:
                # 只保存了片段：PDF 注释里直接显示这段摘录，不再带问答结构
                contents = f"摘录：{markdown_to_plain_text(excerpt)}"
            else:
                contents = (
                    f"问：{markdown_to_plain_text(question)}\n答：{markdown_to_plain_text(answer)}"
                    if question else markdown_to_plain_text(answer)
                )
            if not contents:
                contents = annotation.get("body") or ""
            # Keep the sticky-note icon at the lower-left corner of the anchor.
            point_rect = (
                rect[0],
                max(rect[1], rect[3] - 18),
                min(rect[2], rect[0] + 18),
                rect[3],
            )
            annot = Text(rect=point_rect, text=contents)
            # /Name 让阅读器的“注释列表”把它显示成评论图标；/F 4 = Print，
            # 打印/导出时也带上标记，避免只在屏幕上可见。
            annot[NameObject("/Name")] = NameObject("/Comment")
            annot[NameObject("/F")] = NumberObject(4)
            # 内嵌“AI”圆形徽标外观流：没有 /AP 时，轻量阅读器（浏览器内置、WPS 等）
            # 只会画一个空框，图标就“消失”了。
            appearance = self._ai_badge_appearance(
                writer, float(point_rect[2]) - float(point_rect[0]),
                float(point_rect[3]) - float(point_rect[1]),
            )
            if appearance is not None:
                annot[NameObject("/AP")] = DictionaryObject(
                    {NameObject("/N"): appearance}
                )
            return {
                "annot": self._mark(annot, marker),
                "popup_rect": _popup_rect(rect, width, height),
            }

        if kind == "NOTE":
            contents = str(annotation.get("body") or "").strip()
            # 便签图标放在矩形左下角（显示坐标），内容放进弹出窗。
            point_rect = (
                rect[0],
                max(rect[1], rect[3] - 18),
                min(rect[2], rect[0] + 18),
                rect[3],
            )
            annot = Text(rect=point_rect, text=contents)
            # /Name /Note 是标准便签图标名，与 AI 徽标（/Comment）区分开，
            # 回读时据此分辨两种便签；/F 4 = Print。
            annot[NameObject("/Name")] = NameObject("/Note")
            annot[NameObject("/F")] = NumberObject(4)
            # /C 是便签图标的颜色，写回后回读才能还原自定义颜色。
            annot[NameObject("/C")] = ArrayObject(
                [FloatObject(value) for value in _hex_to_rgb(color or COLOR_HEX["YELLOW"])]
            )
            appearance = self._note_badge_appearance(
                writer, float(point_rect[2]) - float(point_rect[0]),
                float(point_rect[3]) - float(point_rect[1]),
                color or COLOR_HEX["YELLOW"],
            )
            if appearance is not None:
                annot[NameObject("/AP")] = DictionaryObject(
                    {NameObject("/N"): appearance}
                )
            return {
                "annot": self._mark(annot, marker),
                "popup_rect": _popup_rect(rect, width, height),
            }

        return None

    @staticmethod
    def _ai_badge_appearance(writer, width: float, height: float):
        """绘制与应用内一致的“AI”圆形徽标，作为便签注释的外观流（/AP /N）。

        颜色取自应用样式：底 #6f5aa0、描边 #f8f6f0、文字 #f7f4ec。
        使用 Base-14 字体 Helvetica-Bold，无需嵌入字库。
        """
        if writer is None or width <= 0 or height <= 0:
            return None
        size = min(width, height)
        center = size / 2.0
        radius = max(center - size * 0.03, size * 0.1)
        handle = 0.5523 * radius
        left, right = center - radius, center + radius
        bottom, top = center - radius, center + radius
        circle = (
            f"{left:.2f} {center:.2f} m "
            f"{left:.2f} {center - handle:.2f} {center - handle:.2f} {bottom:.2f} "
            f"{center:.2f} {bottom:.2f} c "
            f"{center + handle:.2f} {bottom:.2f} {right:.2f} {center - handle:.2f} "
            f"{right:.2f} {center:.2f} c "
            f"{right:.2f} {center + handle:.2f} {center + handle:.2f} {top:.2f} "
            f"{center:.2f} {top:.2f} c "
            f"{center - handle:.2f} {top:.2f} {left:.2f} {center + handle:.2f} "
            f"{left:.2f} {center:.2f} c h"
        )
        font_size = size * 0.42
        # Helvetica-Bold："A" 722 + "I" 278 = 1000/1000 em，字宽等于字号。
        text_x = (size - font_size) / 2.0
        text_y = (size - font_size * 0.716) / 2.0
        content = (
            "q\n"
            "0.435 0.353 0.627 rg\n"
            f"{circle} f\n"
            "0.971 0.965 0.941 RG\n"
            f"{max(size * 0.035, 0.5):.2f} w\n"
            f"{circle} S\n"
            "Q\n"
            "BT\n"
            f"/F1 {font_size:.2f} Tf\n"
            "0.971 0.965 0.941 rg\n"
            f"{text_x:.2f} {text_y:.2f} Td\n"
            "(AI) Tj\n"
            "ET\n"
        )
        stream = DecodedStreamObject()
        stream.set_data(content.encode("latin-1"))
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Form")
        stream[NameObject("/FormType")] = NumberObject(1)
        stream[NameObject("/BBox")] = ArrayObject(
            [
                FloatObject(0),
                FloatObject(0),
                FloatObject(size),
                FloatObject(size),
            ]
        )
        stream[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
                                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                            }
                        )
                    }
                )
            }
        )
        return writer._add_object(stream)

    @staticmethod
    def _note_badge_appearance(writer, width: float, height: float, hex_color: str):
        """绘制便签图标外观流：填色小方块 + 右上折角，颜色随批注颜色。"""
        if writer is None or width <= 0 or height <= 0:
            return None
        fill = " ".join(f"{value:.3f}" for value in _hex_to_rgb(hex_color))
        fold = min(width, height) * 0.32
        content = (
            "q\n"
            f"{fill} rg\n"
            f"0 0 {width:.2f} {height:.2f} re f\n"
            "0.45 0.38 0.22 RG\n"
            f"{max(width * 0.05, 0.4):.2f} w\n"
            f"0 0 {width:.2f} {height:.2f} re S\n"
            "0.93 0.9 0.8 rg\n"
            f"{width - fold:.2f} {height:.2f} m {width:.2f} {height - fold:.2f} l "
            f"{width - fold:.2f} {height - fold:.2f} l {width - fold:.2f} {height:.2f} l h f\n"
            "Q\n"
        )
        stream = DecodedStreamObject()
        stream.set_data(content.encode("latin-1"))
        stream[NameObject("/Type")] = NameObject("/XObject")
        stream[NameObject("/Subtype")] = NameObject("/Form")
        stream[NameObject("/FormType")] = NumberObject(1)
        stream[NameObject("/BBox")] = ArrayObject(
            [FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]
        )
        return writer._add_object(stream)

    @staticmethod
    def _mark(annot, marker: str):
        annot[NameObject("/NM")] = TextStringObject(marker)
        return annot

    # -- coordinate transforms ----------------------------------------------
    #
    # Reader geometry (quads and rects) is page-normalized 0-1 with the origin
    # at the *displayed* top-left corner, exactly like Reader overlays. PDF
    # user space starts at the bottom-left of the unrotated MediaBox. /Rotate
    # describes how the viewer turns the page for display.

    @staticmethod
    def _page_geometry(page) -> tuple[float, float, int]:
        box = page.mediabox
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        return float(box.width), float(box.height), rotation

    @staticmethod
    def _display_point_to_pdf(nx, ny, rotation, width, height):
        if rotation == 90:
            return ny * width, (1 - nx) * height
        if rotation == 270:
            return (1 - ny) * width, nx * height
        if rotation == 180:
            return (1 - nx) * width, ny * height
        return nx * width, (1 - ny) * height

    @staticmethod
    def _pdf_point_to_display(x, y, rotation, width, height):
        fx = x / width
        fy = y / height
        if rotation == 90:
            return 1 - fy, fx
        if rotation == 270:
            return fy, 1 - fx
        if rotation == 180:
            return 1 - fx, fy
        return fx, 1 - fy

    def _quads_to_pdf(self, quads, width, height, rotation):
        flat = []
        points = []
        for quad in quads:
            corners = [
                self._display_point_to_pdf(point[0], point[1], rotation, width, height)
                for point in quad
            ]
            xs = [x for x, _ in corners]
            ys = [y for _, y in corners]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            # PDF quad point order: upper-left, upper-right, lower-left, lower-right.
            flat.extend((x0, y1, x1, y1, x0, y0, x1, y0))
            points.append(((x0, y0), (x1, y1)))
        rect = [
            min(p[0][0] for p in points),
            min(p[0][1] for p in points),
            max(p[1][0] for p in points),
            max(p[1][1] for p in points),
        ]
        return ArrayObject([FloatObject(value) for value in flat]), rect

    def _pdf_quadpoints_to_display(self, quad_points, width, height, rotation):
        values = [float(value) for value in quad_points]
        quads = []
        for offset in range(0, len(values) - len(values) % 8, 8):
            corners = [
                self._pdf_point_to_display(
                    values[offset + index * 2],
                    values[offset + index * 2 + 1],
                    rotation,
                    width,
                    height,
                )
                for index in range(4)
            ]
            xs = [x for x, _ in corners]
            ys = [y for _, y in corners]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            quads.append([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        return quads

    def _display_rect_to_pdf(self, rect, width, height, rotation):
        x0, y0 = self._display_point_to_pdf(rect[0], rect[1], rotation, width, height)
        x1, y1 = self._display_point_to_pdf(rect[2], rect[3], rotation, width, height)
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]

    def _pdf_rect_to_display(self, rect, width, height, rotation):
        x0, y0 = self._pdf_point_to_display(float(rect[0]), float(rect[1]), rotation, width, height)
        x1, y1 = self._pdf_point_to_display(float(rect[2]), float(rect[3]), rotation, width, height)
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]

    # -- reading ------------------------------------------------------------

    def _restore_annotation(self, annot, page_index, width, height, rotation) -> dict | None:
        subtype = str(annot.get("/Subtype", ""))
        kind = SUBTYPE_TO_KIND.get(subtype)
        if kind is None or rotation not in SUPPORTED_ROTATIONS:
            return None
        # 同为 /Text 便签注解时，用 /Name 区分 AI 解答徽标与普通便签。
        if kind == "AI_NOTE" and str(annot.get("/Name", "")) == "/Note":
            kind = "NOTE"
        annotation_id = str(annot["/NM"])[len(READER_MARKER_PREFIX):]
        quads = None
        rect = None
        if "/QuadPoints" in annot:
            quads = self._pdf_quadpoints_to_display(annot["/QuadPoints"], width, height, rotation)
        elif "/Rect" in annot:
            rect = self._pdf_rect_to_display(annot["/Rect"], width, height, rotation)
        contents = annot.get("/Contents")
        body = str(contents) if contents else None
        return {
            "id": annotation_id,
            "kind": kind,
            "pdf_page_index": page_index,
            "quads": quads,
            "rect": rect,
            "quote": None,
            "body": body,
            "highlight_style": _color_from_annot(annot),
            "foundation_version_at_creation": None,
        }


def _popup_rect(rect, width: float, height: float) -> list[float]:
    popup_width = min(240.0, width * 0.4)
    popup_height = min(160.0, height * 0.3)
    x0 = min(max(rect[0], 0.0), max(width - popup_width, 0.0))
    y0 = min(max(rect[1], 0.0), max(height - popup_height, 0.0))
    return [x0, y0, x0 + popup_width, y0 + popup_height]


def _hex_to_rgb(hex_color: str) -> list[float]:
    value = hex_color.lstrip("#")
    return [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]


def _rgb_to_hex(rgb) -> str | None:
    try:
        channels = [round(float(value) * 255) for value in rgb]
    except (TypeError, ValueError):
        return None
    if len(channels) != 3:
        return None
    return "%02x%02x%02x" % tuple(channels)


def _color_from_annot(annot) -> str | None:
    """读回注解颜色：内置色返回枚举名，自定义色返回 #RRGGBB。"""
    color = annot.get("/C")
    if color is not None:
        hex_value = _rgb_to_hex(list(color))
        if hex_value is None:
            return None
        return _HEX_TO_COLOR.get(hex_value, "#" + hex_value.upper())
    interior = annot.get("/IC")
    if interior is not None:
        hex_value = _rgb_to_hex(list(interior))
        if hex_value is None:
            return None
        return _HEX_TO_COLOR.get(hex_value, "#" + hex_value.upper())
    return None


__all__ = [
    "AnnotationSyncConfig",
    "PdfAnnotationSync",
    "READER_MARKER_PREFIX",
    "SyncReport",
]
