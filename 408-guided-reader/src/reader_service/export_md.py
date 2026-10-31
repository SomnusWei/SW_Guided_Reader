"""Markdown export of durable Q&A assets: Assistant turns, Master topics,
saved explanations and annotation digests. Read-only over the database;
the only write path is the optional archive file under `<data-dir>/exports/`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

_ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f：？＊｜＂＜＞＼／]')
_REVIEW_STATE_LABELS = {
    "PASS": "AI 审查通过",
    "FAIL": "AI 审查未通过",
    "PENDING": "AI 审查进行中",
    "TECHNICAL_FAILURE": "AI 审查暂不可用",
}
_HIGHLIGHT_LABELS = {
    "YELLOW": "黄色",
    "GREEN": "绿色",
    "BLUE": "蓝色",
    "RED": "红色",
    "PURPLE": "紫色",
    "NONE": "无底色",
}
_SOURCE_KIND_LABELS = {
    "USER": "用户批注",
    "AI_SAVED": "AI 保存解释",
}


class MarkdownExporter:
    def __init__(self, database, paths=None):
        self.database = database
        self.paths = paths

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_assistant_conversation(self, revision_id, root, *, node_id=None) -> str:
        """Render one Assistant Root (or one of its Child levels) as Markdown.

        `root` is the public projection from AssistantService.state() — the
        Assistant keeps no durable table, so callers pass the in-memory root
        dict (`{"root_id", "label", "turns", "nodes", "scope", ...}`).
        """
        if not isinstance(root, dict) or not isinstance(root.get("turns"), list):
            raise ValueError("Assistant 会话数据无效。")
        level_turns = root["turns"]
        focus = root.get("label") or ""
        if node_id is not None:
            node = next(
                (item for item in root.get("nodes", []) if item.get("node_id") == node_id),
                None,
            )
            if node is None:
                raise LookupError("这层临时解释已关闭或不存在")
            level_turns = node["turns"]
            focus = node.get("label") or focus
        if not level_turns:
            raise ValueError("这条会话还没有可导出的问答。")

        scope = root.get("scope") or {}
        book = self._book_title(revision_id)
        page_index = scope.get("pdf_page_index")
        lines = [f"# 《{book}》 · 选段问答", ""]
        if focus:
            lines += [f"**解释焦点**：{focus}", ""]
        lines.append(self._source_line(revision_id, page_index, scope.get("chapter_title")))
        if focus:
            lines += ["", f"> {focus}"]
        lines += [""]
        lines += self._qa_sections(level_turns)
        lines.append(f"导出时间：{datetime.now():%Y-%m-%d}")
        return "\n".join(lines).rstrip() + "\n"

    def export_master_topic(self, topic_id) -> str:
        """Render the whole durable Master conversation of one topic."""
        with self.database.connect() as connection:
            topic = connection.execute(
                """
                SELECT t.*, th.book_source_revision_id, th.knowledge_point_id,
                       th.section_outline_node_id
                FROM master_topics t JOIN master_threads th ON th.id = t.thread_id
                WHERE t.id = ?
                """,
                (topic_id,),
            ).fetchone()
            if topic is None:
                raise LookupError("这个 Master 话题不存在。")
            revision_id = topic["book_source_revision_id"]
            subject = self._master_subject(connection, topic)
            section_id = topic["section_outline_node_id"]
            if section_id is None and topic["knowledge_point_id"]:
                owner = connection.execute(
                    "SELECT primary_section_id FROM knowledge_points WHERE knowledge_point_id = ?",
                    (topic["knowledge_point_id"],),
                ).fetchone()
                section_id = owner["primary_section_id"] if owner is not None else None
            messages = connection.execute(
                """
                SELECT role, content, state, created_at FROM master_messages
                WHERE topic_id = ? ORDER BY created_at, rowid
                """,
                (topic_id,),
            ).fetchall()

        chapter_title, section_title = self._outline_titles(revision_id, section_id)
        book = self._book_title(revision_id)
        lines = [f"# 《{book}》 · 知识点学习 · {subject}", ""]
        pieces = [item for item in (chapter_title, section_title) if item]
        lines.append(
            self._source_line(
                revision_id,
                None,
                None,
                date=(topic["created_at"] or "")[:10] or None,
                location=" · ".join(pieces) or None,
            )
        )
        lines.append("")
        if not messages:
            lines += ["（这个话题还没有对话。）", ""]
        round_index = 0
        for message in messages:
            if message["role"] == "user":
                round_index += 1
                if round_index == 1:
                    lines += ["## 问", "", message["content"], ""]
                else:
                    lines += [f"### 追问 {round_index - 1}", "", f"**问**：{message['content']}", ""]
            else:
                if round_index == 0:
                    continue
                if round_index == 1:
                    lines += ["## 答", ""]
                else:
                    lines += ["**答**：", ""]
                lines += [message["content"], ""]
                if message["state"] != "COMPLETE":
                    lines += [f"> （这条回答未完成：{message['state']}）", ""]
        return "\n".join(lines).rstrip() + "\n"

    def export_saved_explanation(self, explanation_id) -> str:
        """Render one durable AI_SAVED explanation (Annotation) as Markdown."""
        annotation = self._load_annotation(explanation_id)
        if annotation["source_kind"] != "AI_SAVED":
            raise ValueError("只有 AI 保存的解释可以按此导出。")
        revision_id = annotation["book_source_revision_id"]
        book = self._book_title(revision_id)
        grounding = annotation["source_grounding"] or {}
        question, answer, quote, page_index = self._qa_parts(annotation)
        lines = [f"# 《{book}》 · 已保存的解释", ""]
        lines.append(
            self._source_line(
                revision_id,
                page_index,
                grounding.get("chapter_title"),
                date=(annotation["created_at"] or "")[:10] or None,
            )
        )
        if quote:
            lines += ["", f"> {quote}"]
        lines += ["", "## 问", "", question, ""]
        lines += ["## 答", "", answer, ""]
        state = _REVIEW_STATE_LABELS.get(annotation["verification_state"])
        if state:
            lines += [f"AI 审查：{state}", ""]
        return "\n".join(lines).rstrip() + "\n"

    def export_annotations(self, revision_id) -> str:
        """Render every annotation of one revision, sorted by PDF page."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM annotations
                WHERE book_source_revision_id = ?
                ORDER BY pdf_page_index, created_at, id
                """,
                (revision_id,),
            ).fetchall()
        annotations = [_annotation_row(row) for row in rows]
        book = self._book_title(revision_id)
        lines = [f"# 《{book}》 · 批注汇总", ""]
        if not annotations:
            lines += ["（这本书还没有批注。）", ""]
        current_page = None
        ordinal = 0
        for annotation in annotations:
            page_number = annotation["pdf_page_index"] + 1
            if page_number != current_page:
                current_page = page_number
                lines += [f"## 第 {page_number} 页", ""]
            ordinal += 1
            lines += self._annotation_section(annotation, ordinal)
        return "\n".join(lines).rstrip() + "\n"

    def export_bundle(
        self,
        revision_id,
        *,
        assistant_roots=(),
        master_topic_ids=(),
        explanation_ids=(),
        include_annotations=False,
    ) -> str:
        """Combine any of the exportable scopes of one revision into one file."""
        book = self._book_title(revision_id)
        lines = [f"# 《{book}》 · 学习导出", "", f"导出时间：{datetime.now():%Y-%m-%d}", ""]
        parts = []
        for root in assistant_roots:
            parts.append(self.export_assistant_conversation(revision_id, root))
        for topic_id in master_topic_ids:
            parts.append(self.export_master_topic(topic_id))
        for explanation_id in explanation_ids:
            parts.append(self.export_saved_explanation(explanation_id))
        if include_annotations:
            parts.append(self.export_annotations(revision_id))
        if not parts:
            raise ValueError("导出范围至少要包含一项内容。")
        for part in parts:
            lines += ["---", "", part, ""]
        return "\n".join(lines).rstrip() + "\n"

    def build_filename(
        self, revision_id, *, page_index=None, ordinal=None, topic=None
    ) -> str:
        """`{书名}_{页码或序号}_{主题slug}_{YYYYMMDD}.md`."""
        pieces = [self._book_title(revision_id)]
        if page_index is not None:
            pieces.append(f"第{page_index + 1}页")
        elif ordinal is not None:
            pieces.append(f"第{ordinal}条")
        slug = _slug(topic)
        if slug:
            pieces.append(slug)
        pieces.append(f"{datetime.now():%Y%m%d}")
        name = "_".join(pieces)
        return _sanitize_filename(name) + ".md"

    def save_to_data_dir(self, revision_id, content, filename) -> Path:
        """Archive one export under `<data-dir>/exports/`; dedupes the name."""
        if self.paths is None:
            raise ValueError("导出器未配置数据目录，无法存档。")
        return _write_markdown(self.paths.resolve("exports"), content, filename)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _qa_sections(self, turns) -> list[str]:
        lines = []
        for index, turn in enumerate(turns):
            question = turn.get("question") or ""
            answer = turn.get("answer") or ""
            if index == 0:
                lines += ["## 问", "", question, "", "## 答", "", answer, ""]
            else:
                lines += [
                    f"### 追问 {index}",
                    "",
                    f"**问**：{question}",
                    "",
                    "**答**：",
                    "",
                    answer,
                    "",
                ]
        return lines

    def _annotation_section(self, annotation, ordinal) -> list[str]:
        kind = _SOURCE_KIND_LABELS.get(annotation["source_kind"], annotation["source_kind"])
        color = _HIGHLIGHT_LABELS.get(annotation["highlight_style"], annotation["highlight_style"])
        _question, _answer, quote, _page = self._qa_parts(annotation)
        lines = [f"### 批注 {ordinal}（{kind} · {color}）", ""]
        if quote:
            lines += [f"> {quote}", ""]
        if annotation["source_kind"] == "AI_SAVED":
            question, answer, _quote, _page = self._qa_parts(annotation)
            excerpt = str((annotation.get("payload") or {}).get("excerpt") or "").strip()
            if excerpt:
                lines += ["**摘录自 AI 回答**：", "", excerpt, ""]
                if question:
                    lines += [f"> 当时的问题：{question}", ""]
            else:
                lines += ["**问**：", "", question, ""]
                lines += ["**答**：", "", answer, ""]
                state = _REVIEW_STATE_LABELS.get(annotation["verification_state"])
                if state:
                    lines += [f"AI 审查：{state}", ""]
        elif annotation["body"]:
            lines += [annotation["body"], ""]
        return lines

    def _load_annotation(self, annotation_id) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM annotations WHERE id = ?", (annotation_id,)
            ).fetchone()
            if row is None:
                raise LookupError("这条批注不存在。")
            return _annotation_row(row)

    @staticmethod
    def _qa_parts(annotation) -> tuple[str, str, str, int]:
        """payload (AI_NOTE) 优先，缺失时回退 provenance + body。"""
        payload = annotation.get("payload") or {}
        provenance = annotation.get("provenance") or {}
        question = payload.get("question") or provenance.get("answer_question") or ""
        answer = payload.get("answer") or annotation.get("body") or ""
        quote = annotation.get("quote") or payload.get("quote") or ""
        page_index = annotation.get("pdf_page_index")
        if page_index is None:
            page_index = payload.get("page_index")
        return question, answer, quote, page_index

    def _master_subject(self, connection, topic) -> str:
        if topic["knowledge_point_id"]:
            row = connection.execute(
                "SELECT title FROM knowledge_points WHERE knowledge_point_id = ?",
                (topic["knowledge_point_id"],),
            ).fetchone()
            if row is not None:
                return row["title"]
        return self._section_title(
            topic["book_source_revision_id"], topic["section_outline_node_id"]
        ) or "知识点对话"

    def _section_title(self, revision_id, section_outline_node_id):
        return self._outline_titles(revision_id, section_outline_node_id)[1]

    def _outline_titles(self, revision_id, section_outline_node_id):
        if not section_outline_node_id:
            return None, None
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT title, parent_id FROM outline_nodes
                WHERE book_source_revision_id = ? AND outline_node_id = ?
                """,
                (revision_id, section_outline_node_id),
            ).fetchone()
            if row is None:
                return None, None
            chapter_title = None
            if row["parent_id"]:
                parent = connection.execute(
                    "SELECT title FROM outline_nodes WHERE outline_node_id = ?",
                    (row["parent_id"],),
                ).fetchone()
                chapter_title = parent["title"] if parent is not None else None
            return chapter_title, row["title"]

    def _book_title(self, revision_id) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT b.title FROM book_source_revisions r
                JOIN books b ON b.id = r.book_id WHERE r.id = ?
                """,
                (revision_id,),
            ).fetchone()
        return row["title"] if row is not None else revision_id[:8]

    def _printed_label(self, revision_id, page_index):
        if page_index is None:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT printed_label FROM page_labels
                WHERE book_source_revision_id = ? AND pdf_page_index = ?
                  AND printed_label IS NOT NULL
                """,
                (revision_id, page_index),
            ).fetchone()
        return row["printed_label"] if row is not None else None

    def _source_line(
        self, revision_id, page_index, chapter_title, *, date=None, location=None
    ) -> str:
        pieces = []
        if page_index is not None:
            page_text = f"第 {page_index + 1} 页"
            printed = self._printed_label(revision_id, page_index)
            if printed:
                page_text += f"（印刷页码 {printed}）"
            pieces.append(page_text)
        if chapter_title:
            pieces.append(f"章节 {chapter_title}")
        if location:
            pieces.append(location)
        if date:
            pieces.append(date)
        return "来源：" + (" · ".join(pieces) if pieces else "未知")


def _write_markdown(directory: Path, content: str, filename: str) -> Path:
    """把 Markdown 写进 directory，重名自动追加序号；返回实际路径。"""
    directory.mkdir(parents=True, exist_ok=True)
    name = _sanitize_filename(filename)
    if not name.endswith(".md"):
        name += ".md"
    destination = directory / name
    stem, suffix = destination.stem, destination.suffix
    counter = 2
    while destination.exists():
        destination = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    destination.write_text(content, encoding="utf-8")
    return destination


def save_markdown_to_downloads(content: str, filename: str) -> Path:
    """桌面外壳（Tauri/WebView2）会静默丢弃 blob 锚点下载，因此提供由本地
    服务直接写系统「下载」文件夹的通道；返回实际保存路径。"""
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        raise ValueError("未找到系统「下载」文件夹，无法保存导出文件。")
    return _write_markdown(downloads, content, filename)


def save_markdown_to_path(content: str, path: Path) -> Path:
    """把 Markdown 写到用户在「另存为」对话框里选定的完整路径。

    无扩展名时补 .md；路径已存在时直接覆盖（对话框的覆盖确认由
    OFN_OVERWRITEPROMPT 负责），不再追加序号——用户点名要的路径要尊重。
    """
    target = path.expanduser()
    if not target.suffix:
        target = target.with_suffix(".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _annotation_row(row) -> dict:
    """Only the annotation fields the exporter renders.

    Read straight from the sqlite row (no quads parsing) so the export keeps
    working across the kind/rect/payload schema extension.
    """
    keys = set(row.keys())

    def get(name):
        return row[name] if name in keys else None

    def parsed(name):
        raw = get(name)
        return json.loads(raw) if raw else None

    return {
        "id": get("id"),
        "book_source_revision_id": get("book_source_revision_id"),
        "pdf_page_index": get("pdf_page_index"),
        "kind": get("kind"),
        "quote": get("quote"),
        "body": get("body"),
        "highlight_style": get("highlight_style"),
        "source_kind": get("source_kind"),
        "verification_state": get("verification_state"),
        "provenance": parsed("provenance_json"),
        "source_grounding": parsed("source_grounding_json"),
        "payload": parsed("payload_json"),
        "created_at": get("created_at"),
    }


def _slug(value) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    cleaned = _ILLEGAL_FILENAME.sub("", " ".join(value.split()))
    cleaned = cleaned.strip(" .-_#")
    return cleaned[:40]


def _sanitize_filename(name) -> str:
    cleaned = _ILLEGAL_FILENAME.sub("", str(name)).strip(" .")
    return cleaned or "export"


__all__ = ["MarkdownExporter"]
