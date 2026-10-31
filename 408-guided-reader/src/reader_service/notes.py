"""Markdown 笔记（迁移 32）。

全局笔记本：每条笔记可以可选地关联一本教材与页码，用于追溯来源；教材被
删除时笔记保留、关联置空（``ON DELETE SET NULL``），书名快照列为空但历史
文本仍在导出内容里。

笔记只写本地 ``state.sqlite3``，不经过任何外部服务。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from uuid import uuid4

from reader_service.notes_schema import NOTES_SCHEMA

BODY_LIMIT = 200_000
TITLE_LIMIT = 120
EXCERPT_LIMIT = 80
DERIVED_TITLE_LIMIT = 40

_UNSET = object()

# 标题里的路径非法字符，导出文件名要替换掉
_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]')
# Markdown 行首标记（标题、引用、列表、任务列表），用于提取纯文本摘要
_LINE_MARKERS = re.compile(r"^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s*)+")


def _clean_body(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("笔记正文必须是文本。")
    if len(value) > BODY_LIMIT:
        raise ValueError(f"笔记正文过长（上限 {BODY_LIMIT:,} 字符）。")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _plain_line(text: str) -> str:
    for line in text.splitlines():
        stripped = _LINE_MARKERS.sub("", line).strip()
        if stripped:
            # 去掉行内代码与强调标记，保留可读文字
            return stripped.replace("`", "").replace("**", "").replace("__", "")
    return ""


def _excerpt(body: str) -> str:
    line = _plain_line(body)
    return line[:EXCERPT_LIMIT]


def _derive_title(body: str) -> str:
    line = _plain_line(body)
    return line[:DERIVED_TITLE_LIMIT] if line else "未命名笔记"


def _clean_title(value, body: str) -> str:
    if value is None:
        return _derive_title(body)
    if not isinstance(value, str):
        raise ValueError("笔记标题必须是文本。")
    text = value.strip()
    if not text:
        return _derive_title(body)
    return text[:TITLE_LIMIT]


def _clean_page_index(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("页码必须是整数。")
    if value < 0:
        raise ValueError("页码不能是负数。")
    return value


def _clean_pinned(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError("置顶状态无效。")


def _friendly_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value


class NotesService:
    """SQLite-backed Markdown notebook (global, optional book reference)."""

    def __init__(self, database):
        self.database = database
        self._lock = threading.Lock()

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _row(row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "body": row["body"],
            "excerpt": row["excerpt"],
            "book_source_revision_id": row["book_source_revision_id"],
            "page_index": row["page_index"],
            "book_title_snapshot": row["book_title_snapshot"],
            "pinned": bool(row["pinned"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _summary(row) -> dict:
        return {
            "id": row["id"],
            "title": row["title"],
            "excerpt": row["excerpt"],
            "book_source_revision_id": row["book_source_revision_id"],
            "page_index": row["page_index"],
            "book_title_snapshot": row["book_title_snapshot"],
            "pinned": bool(row["pinned"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _resolve_book(connection, revision_id):
        """Return ``(revision_id, title_snapshot)``; raise when unknown."""
        if revision_id in (None, ""):
            return None, None
        if not isinstance(revision_id, str):
            raise ValueError("关联教材无效。")
        row = connection.execute(
            """
            SELECT r.id, b.title
            FROM book_source_revisions r
            JOIN books b ON b.id = r.book_id
            WHERE r.id = ?
            """,
            (revision_id,),
        ).fetchone()
        if row is None:
            raise ValueError("关联的教材不存在或已被删除。")
        return row["id"], row["title"]

    def _fetch(self, connection, note_id: str):
        row = connection.execute("SELECT * FROM notes WHERE id = ?", (str(note_id),)).fetchone()
        if row is None:
            raise LookupError("这条笔记不存在或已被删除。")
        return row

    # -- read path ----------------------------------------------------------

    def list_notes(
        self,
        query: str | None = None,
        book: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        text = (query or "").strip()
        try:
            size = max(1, min(int(limit if limit is not None else 50), 200))
            start = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise ValueError("分页参数无效。") from None
        where: list[str] = []
        params: list = []
        if text:
            where.append(
                "(instr(lower(title), lower(?)) > 0"
                " OR instr(lower(body), lower(?)) > 0"
                " OR instr(lower(excerpt), lower(?)) > 0)"
            )
            params.extend([text, text, text])
        if book:
            where.append("book_source_revision_id = ?")
            params.append(str(book))
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM notes{clause}", tuple(params)
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT id, title, excerpt, book_source_revision_id, page_index,
                       book_title_snapshot, pinned, created_at, updated_at
                FROM notes{clause}
                ORDER BY pinned DESC, updated_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (*params, size, start),
            ).fetchall()
        return {"notes": [self._summary(row) for row in rows], "total": total}

    def get(self, note_id: str) -> dict:
        with self.database.connect() as connection:
            return self._row(self._fetch(connection, note_id))

    # -- write path ---------------------------------------------------------

    def create(
        self,
        title=None,
        body=None,
        book_source_revision_id=None,
        page_index=None,
        pinned=None,
    ) -> dict:
        text = _clean_body(body)
        clean_title = _clean_title(title, text)
        clean_page = _clean_page_index(page_index)
        clean_pinned = 0 if pinned is None else _clean_pinned(pinned)
        now = datetime.now().isoformat(timespec="seconds")
        note_id = str(uuid4())
        with self._lock:
            with self.database.connect() as connection:
                revision_id, snapshot = self._resolve_book(connection, book_source_revision_id)
                connection.execute(
                    """
                    INSERT INTO notes
                        (id, title, body, excerpt, book_source_revision_id, page_index,
                         book_title_snapshot, pinned, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        note_id,
                        clean_title,
                        text,
                        _excerpt(text),
                        revision_id,
                        clean_page,
                        snapshot,
                        clean_pinned,
                        now,
                        now,
                    ),
                )
                return self._row(self._fetch(connection, note_id))

    def update(
        self,
        note_id: str,
        *,
        title=_UNSET,
        body=_UNSET,
        pinned=_UNSET,
        book_source_revision_id=_UNSET,
        page_index=_UNSET,
    ) -> dict:
        if all(
            value is _UNSET
            for value in (title, body, pinned, book_source_revision_id, page_index)
        ):
            raise ValueError("没有可更新的笔记字段。")
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self.database.connect() as connection:
                row = self._fetch(connection, note_id)
                changes: dict[str, object] = {}

                if body is not _UNSET:
                    text = _clean_body(body)
                    changes["body"] = text
                    changes["excerpt"] = _excerpt(text)
                else:
                    text = row["body"]

                if title is not _UNSET:
                    changes["title"] = _clean_title(title, text)
                elif body is not _UNSET and row["title"] in (
                    "未命名笔记",
                    _derive_title(row["body"]),
                ):
                    # 标题还是自动派生的：正文换了首行就跟着更新，不打断用户输入
                    derived = _derive_title(text)
                    if derived != row["title"]:
                        changes["title"] = derived

                if pinned is not _UNSET:
                    changes["pinned"] = _clean_pinned(pinned)

                if book_source_revision_id is not _UNSET:
                    revision_id, snapshot = self._resolve_book(
                        connection, book_source_revision_id
                    )
                    changes["book_source_revision_id"] = revision_id
                    changes["book_title_snapshot"] = snapshot

                if page_index is not _UNSET:
                    changes["page_index"] = _clean_page_index(page_index)

                assignments = ", ".join(f"{name} = ?" for name in changes)
                connection.execute(
                    f"UPDATE notes SET {assignments}, updated_at = ? WHERE id = ?",
                    (*changes.values(), now, str(note_id)),
                )
                return self._row(self._fetch(connection, note_id))

    def delete(self, note_id: str) -> None:
        with self._lock:
            with self.database.connect() as connection:
                cursor = connection.execute("DELETE FROM notes WHERE id = ?", (str(note_id),))
                if cursor.rowcount == 0:
                    raise LookupError("这条笔记不存在或已被删除。")

    # -- export -------------------------------------------------------------

    @staticmethod
    def _source_line(note: dict) -> str | None:
        parts = []
        if note.get("book_title_snapshot"):
            parts.append(str(note["book_title_snapshot"]))
        if isinstance(note.get("page_index"), int):
            parts.append(f"第 {note['page_index'] + 1} 页")
        if not parts:
            return None
        return "来源：" + " · ".join(parts)

    @staticmethod
    def _safe_filename(text: str, fallback: str) -> str:
        cleaned = _FILENAME_UNSAFE.sub("_", text or "").strip(" ._")
        return cleaned[:60] or fallback

    def export_one(self, note_id: str) -> tuple[str, str]:
        note = self.get(note_id)
        stamp = datetime.now().strftime("%Y%m%d")
        lines = [f"# {note['title']}", ""]
        source = self._source_line(note)
        if source:
            lines.append(f"> {source}")
        lines.append(f"> 更新时间：{_friendly_time(note['updated_at'])}")
        lines.extend(["", note["body"].strip() or "（正文为空）", ""])
        filename = f"笔记_{self._safe_filename(note['title'], '未命名笔记')}_{stamp}.md"
        return "\n".join(lines), filename

    def export_all(self) -> tuple[str, str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC, rowid DESC"
            ).fetchall()
        notes = [self._row(row) for row in rows]
        stamp = datetime.now().strftime("%Y%m%d")
        lines = [
            "# 笔记本",
            "",
            f"> 导出时间：{_friendly_time(datetime.now().isoformat(timespec='seconds'))}"
            f" · 共 {len(notes)} 条笔记",
            "",
        ]
        if not notes:
            lines.extend(["（还没有任何笔记）", ""])
        for note in notes:
            lines.extend(["---", "", f"## {note['title']}", ""])
            source = self._source_line(note)
            if source:
                lines.append(f"> {source}")
            lines.append(f"> 更新时间：{_friendly_time(note['updated_at'])}")
            lines.extend(["", note["body"].strip() or "（正文为空）", ""])
        return "\n".join(lines), f"笔记本_{stamp}.md"


__all__ = ["NotesService", "NOTES_SCHEMA"]
