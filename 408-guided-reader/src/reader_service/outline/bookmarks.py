"""PDF 自带书签（outline）导入通道。

与 OCR 目录文本通道相互独立：这里只读取 PDF 内嵌书签树，把每个书签
解析为一个扁平节点（含层级），供 OutlineService 组树并写入大纲表。
单个书签解析失败只记录并跳过，不中断整体导入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import Destination

logger = logging.getLogger(__name__)

BOOKMARK_PARSER_VERSION = "pdf-bookmarks-v1"

# 乱码修复候选：PDF 生成器把 GBK / UTF-8 / UTF-16 字节按 Latin-1 写入时，
# pypdf 返回的标题是单字节区间的伪文本，可以原样编码回字节再正确解码。
_MOJIBAKE_CANDIDATES = ("gbk", "utf-8", "utf-16")


def _looks_mojibake(title: str) -> bool:
    """Latin-1 高位区（U+0080–U+00FF）字符占满非空字符时才是典型乱码。"""
    if "\ufffd" in title:
        return True
    significant = [c for c in title if not c.isspace()]
    return bool(significant) and all(0x80 <= ord(c) <= 0xFF for c in significant)


def repair_title(title: str) -> tuple[str, bool]:
    """Try to recover a mojibake bookmark title; return (title, needs_review)."""
    cleaned = " ".join(str(title).split()).strip()
    if not cleaned or not _looks_mojibake(cleaned):
        return cleaned, "\ufffd" in cleaned
    try:
        raw = cleaned.encode("latin-1")
    except UnicodeEncodeError:
        return cleaned, True
    for encoding in _MOJIBAKE_CANDIDATES:
        try:
            repaired = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if repaired != cleaned and any("\u4e00" <= c <= "\u9fff" for c in repaired):
            return " ".join(repaired.split()), True
    return cleaned, True


def _destination_top(reader: PdfReader, item) -> float | None:
    """Normalized top offset (0 = page top, 1 = page bottom) when available."""
    try:
        array = item.dest_array
        if len(array) < 4 or str(array[1]) != "/XYZ":
            return None
        top = float(array[3])
        page_number = reader.get_destination_page_number(item)
        height = float(reader.pages[page_number].mediabox.height)
        if height <= 0:
            return None
        return max(0.0, min(1.0, 1.0 - top / height))
    except Exception:  # noqa: BLE001 - Y offset is best-effort metadata only.
        return None


class BookmarkImporter:
    """Parse the embedded outline tree of a PDF into flat bookmark nodes."""

    def __init__(self):
        self.skipped: list[dict] = []

    def has_bookmarks(self, pdf_path: Path) -> bool:
        """True when the PDF carries at least one usable outline entry."""
        try:
            with pdf_path.open("rb") as stream:
                reader = PdfReader(stream, strict=False)
                return self._count_entries(reader.outline) > 0
        except Exception:  # noqa: BLE001 - unreadable PDF simply has no bookmarks.
            return False

    def import_bookmarks(self, pdf_path: Path) -> list[dict]:
        """Return flat nodes: {title, page_index, dest_y, level, path, needs_review}."""
        self.skipped = []
        nodes: list[dict] = []
        # Own and close the stream deterministically: Windows otherwise keeps the
        # source PDF undeletable until a PdfReader happens to be garbage-collected.
        with pdf_path.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            self._walk(reader, reader.outline, 0, (), nodes)
        return nodes

    @staticmethod
    def _count_entries(items) -> int:
        if not items:
            return 0
        return sum(
            1 if isinstance(item, Destination)
            else BookmarkImporter._count_entries(item)
            for item in items
        )

    def _walk(
        self,
        reader: PdfReader,
        items,
        level: int,
        path: tuple[int, ...],
        nodes: list[dict],
    ) -> None:
        sibling = 0
        for item in items:
            if isinstance(item, list):
                self._walk(reader, item, level + 1, path + (max(0, sibling - 1),), nodes)
                continue
            try:
                nodes.append(self._parse(reader, item, level, path + (sibling,)))
            except Exception as exc:  # noqa: BLE001 - one bad bookmark must not abort the import.
                self.skipped.append(
                    {"title": str(getattr(item, "title", "") or ""), "path": list(path + (sibling,)), "error": str(exc)}
                )
                logger.warning("Skipped unresolvable PDF bookmark at %s: %s", path + (sibling,), exc)
            sibling += 1

    def _parse(self, reader: PdfReader, item, level: int, path: tuple[int, ...]) -> dict:
        title, needs_review = repair_title(getattr(item, "title", ""))
        if not title:
            raise ValueError("bookmark has an empty title")
        try:
            page_index = reader.get_destination_page_number(item)
            if not isinstance(page_index, int) or page_index < 0:
                raise ValueError(f"invalid destination page: {page_index!r}")
        except Exception:
            page_index = None
            needs_review = True
        return {
            "title": title,
            "page_index": page_index,
            "dest_y": _destination_top(reader, item) if page_index is not None else None,
            "level": level,
            "path": path,
            "needs_review": needs_review,
        }
