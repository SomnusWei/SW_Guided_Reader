from __future__ import annotations

import json
from datetime import UTC, datetime

from reader_service.library.database import Database

# 数据来源通道：PDF_BOOKMARK 为内嵌书签导入；OCR 为 OCR 证据构建
# （历史 bootstrap 数据的默认值，补列时沿用）；MANUAL 为手动维护。
_BOOKMARK_NODE_SOURCE = "PDF_BOOKMARK"
# outline_bootstrap_records 的 evidence_source 受 CHECK 约束，只允许既有词汇。
_BOOKMARK_EVIDENCE_SOURCE = "BOOKMARK"


def _ensure_source_column(connection) -> None:
    """迁移式补列：老库的 outline_nodes 没有 source 列时补上，幂等。"""
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(outline_nodes)")}
    if "source" not in columns:
        connection.execute(
            "ALTER TABLE outline_nodes ADD COLUMN source TEXT NOT NULL DEFAULT 'OCR'"
        )


def now() -> str:
    return datetime.now(UTC).isoformat()


class OutlineRepository:
    def __init__(self, database: Database):
        self.database = database

    def ready_snapshot(self, revision_id: str) -> tuple[list[dict], dict[int, list[dict]]]:
        with self.database.connect() as connection:
            statuses = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT pdf_page_index, status FROM ocr_pages
                    WHERE book_source_revision_id = ? ORDER BY pdf_page_index
                    """,
                    (revision_id,),
                )
            ]
            pages: dict[int, list[dict]] = {}
            for row in connection.execute(
                """
                SELECT lines.pdf_page_index, lines.line_ordinal, lines.text,
                       lines.confidence, lines.quad_json
                FROM ocr_lines AS lines JOIN ocr_pages AS pages
                  ON pages.book_source_revision_id = lines.book_source_revision_id
                 AND pages.pdf_page_index = lines.pdf_page_index
                WHERE lines.book_source_revision_id = ? AND pages.status = 'READY'
                ORDER BY lines.pdf_page_index, lines.line_ordinal
                """,
                (revision_id,),
            ):
                value = dict(row)
                value["quad"] = json.loads(value.pop("quad_json"))
                pages.setdefault(row["pdf_page_index"], []).append(value)
            return statuses, pages

    def list(self, revision_id: str) -> list[dict]:
        with self.database.connect() as connection:
            _ensure_source_column(connection)
            rows = connection.execute(
                """
                SELECT book_source_revision_id, outline_node_id, identity_revision, parent_id, depth, order_index,
                       kind, title, printed_label_hint, start_page, start_y, end_page, end_y,
                       resolution_state, physical_revision, confidence, evidence_json, source
                FROM outline_nodes WHERE book_source_revision_id = ?
                ORDER BY depth, parent_id, order_index, outline_node_id
                """,
                (revision_id,),
            )
            result = []
            for row in rows:
                value = dict(row)
                value["evidence"] = json.loads(value.pop("evidence_json"))
                result.append(value)
            return result

    def bootstrap_record(self, revision_id: str) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM outline_bootstrap_records WHERE book_source_revision_id = ?",
                (revision_id,),
            ).fetchone()
            return dict(row) if row else None

    def commit(
        self,
        revision_id: str,
        nodes: list[dict],
        *,
        parser_version: str,
        evidence_source: str,
        evidence_digest: str,
        structure_digest: str,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM outline_nodes WHERE book_source_revision_id = ? LIMIT 1",
                (revision_id,),
            ).fetchone()
            if exists:
                raise RuntimeError("Outline logical tree was already minted")
            connection.executemany(
                """
                INSERT INTO outline_nodes(
                    outline_node_id, book_source_revision_id, identity_revision,
                    parent_id, depth, order_index, kind, title, printed_label_hint,
                    start_page, start_y, end_page, end_y, resolution_state,
                    physical_revision, confidence, evidence_json
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, 1, ?, ?)
                """,
                (
                    (
                        node["outline_node_id"], revision_id, node["parent_id"], node["depth"],
                        node["order_index"], node["kind"], node["title"],
                        node["printed_label_hint"], node["start_page"],
                        "PARTIAL" if node["start_page"] is not None else "UNRESOLVED",
                        node["confidence"], json.dumps(node["evidence"], ensure_ascii=False),
                    )
                    for node in nodes
                ),
            )
            connection.execute(
                """
                INSERT INTO outline_bootstrap_records(
                    book_source_revision_id, parser_version, evidence_source,
                    evidence_digest, structure_digest, last_conflict_digest, committed_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    revision_id, parser_version, evidence_source, evidence_digest,
                    structure_digest, now(),
                ),
            )

    def force_recommit(
        self,
        revision_id: str,
        nodes: list[dict],
        *,
        parser_version: str,
        evidence_source: str,
        evidence_digest: str,
        structure_digest: str,
    ) -> None:
        """覆盖式重提交：清除旧大纲树与 bootstrap 记录后重新写入。

        仅用于证据通道升级（坏书签 -> 目录 OCR）这类刻意重建，不参与
        常规 bootstrap 流程；会丢弃引用旧章节节点 ID 的学习状态。
        """
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM outline_nodes WHERE book_source_revision_id = ?",
                (revision_id,),
            )
            connection.execute(
                "DELETE FROM outline_bootstrap_records WHERE book_source_revision_id = ?",
                (revision_id,),
            )
        self.commit(
            revision_id,
            nodes,
            parser_version=parser_version,
            evidence_source=evidence_source,
            evidence_digest=evidence_digest,
            structure_digest=structure_digest,
        )

    def record_conflict(self, revision_id: str, conflict_digest: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE outline_bootstrap_records SET last_conflict_digest = ?
                WHERE book_source_revision_id = ?
                """,
                (conflict_digest, revision_id),
            )

    def apply_targets(self, revision_id: str, targets: dict[str, int | None]) -> None:
        """Apply only physical start evidence; logical identity fields are never touched."""
        with self.database.connect() as connection:
            for node_id, page_index in targets.items():
                connection.execute(
                    """
                    UPDATE outline_nodes
                    SET start_page = ?, resolution_state = CASE
                        WHEN ? IS NULL THEN 'UNRESOLVED' ELSE 'PARTIAL' END
                    WHERE book_source_revision_id = ? AND outline_node_id = ?
                      AND resolution_state != 'RESOLVED'
                      AND (start_page IS NOT ? OR resolution_state != CASE
                          WHEN ? IS NULL THEN 'UNRESOLVED' ELSE 'PARTIAL' END)
                    """,
                    (
                        page_index, page_index, revision_id, node_id,
                        page_index, page_index,
                    ),
                )

    def resolve_chapter_ranges(
        self,
        revision_id: str,
        chapter_id: str,
        resolutions: dict[str, dict],
        identity_snapshot: tuple[tuple, ...],
    ) -> list[dict]:
        """Atomically advance only one Chapter subtree's physical fields."""
        timestamp = now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                WITH RECURSIVE subtree(outline_node_id) AS (
                    SELECT outline_node_id FROM outline_nodes
                    WHERE book_source_revision_id = ? AND outline_node_id = ?
                    UNION ALL
                    SELECT child.outline_node_id
                    FROM outline_nodes AS child
                    JOIN subtree ON child.parent_id = subtree.outline_node_id
                    WHERE child.book_source_revision_id = ?
                )
                SELECT outline_node_id, parent_id, depth, order_index, kind, title,
                       identity_revision, start_page, start_y, end_page, end_y,
                       resolution_state, physical_revision, evidence_json
                FROM outline_nodes
                WHERE book_source_revision_id = ?
                  AND outline_node_id IN (SELECT outline_node_id FROM subtree)
                ORDER BY depth, parent_id, order_index, outline_node_id
                """,
                (revision_id, chapter_id, revision_id, revision_id),
            ).fetchall()
            current_identity = tuple(
                (
                    row["outline_node_id"], row["parent_id"], row["depth"],
                    row["order_index"], row["kind"], row["title"],
                    row["identity_revision"],
                )
                for row in rows
            )
            if current_identity != identity_snapshot:
                raise RuntimeError("Outline logical identity changed during Chapter preparation")
            current_ids = {row["outline_node_id"] for row in rows}
            if not resolutions or not set(resolutions).issubset(current_ids):
                raise RuntimeError("Chapter resolution escaped the requested subtree")
            for row in rows:
                target = resolutions.get(row["outline_node_id"])
                if target is None:
                    continue
                values = (
                    target["start_page"], target["start_y"],
                    target["end_page"], target["end_y"], "RESOLVED",
                )
                current = (
                    row["start_page"], row["start_y"], row["end_page"],
                    row["end_y"], row["resolution_state"],
                )
                if current == values:
                    continue
                evidence = json.loads(row["evidence_json"])
                evidence["physical_resolution"] = {
                    "source": "TARGET_CHAPTER_OCR",
                    "resolved_at": timestamp,
                    "heading_ref": target["heading_ref"],
                }
                connection.execute(
                    """
                    UPDATE outline_nodes
                    SET start_page = ?, start_y = ?, end_page = ?, end_y = ?,
                        resolution_state = 'RESOLVED',
                        physical_revision = physical_revision + 1,
                        confidence = MIN(confidence, ?), evidence_json = ?
                    WHERE book_source_revision_id = ? AND outline_node_id = ?
                    """,
                    (
                        target["start_page"], target["start_y"],
                        target["end_page"], target["end_y"],
                        target["confidence"],
                        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                        revision_id, row["outline_node_id"],
                    ),
                )
        return self.list(revision_id)

    def write_bookmarks(
        self,
        revision_id: str,
        nodes: list[dict],
        *,
        parser_version: str,
        evidence_digest: str,
        structure_digest: str,
    ) -> None:
        """先清后写书签来源大纲（幂等）；不触碰 OCR / 手动来源数据。

        同时维护 outline_bootstrap_records：书签来源记录存在时更新摘要，
        其他来源记录保留不动（只落书签节点），避免覆盖 OCR 证据通道
        的构建事实。
        """
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _ensure_source_column(connection)
            connection.execute(
                "DELETE FROM outline_nodes WHERE book_source_revision_id = ? AND source = ?",
                (revision_id, _BOOKMARK_NODE_SOURCE),
            )
            record = connection.execute(
                "SELECT evidence_source FROM outline_bootstrap_records WHERE book_source_revision_id = ?",
                (revision_id,),
            ).fetchone()
            for node in nodes:
                self._insert_bookmark_node(connection, revision_id, node)
            if record is not None and record["evidence_source"] != _BOOKMARK_EVIDENCE_SOURCE:
                return
            if record is None:
                connection.execute(
                    """
                    INSERT INTO outline_bootstrap_records(
                        book_source_revision_id, parser_version, evidence_source,
                        evidence_digest, structure_digest, last_conflict_digest, committed_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        revision_id, parser_version, _BOOKMARK_EVIDENCE_SOURCE,
                        evidence_digest, structure_digest, now(),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE outline_bootstrap_records
                    SET parser_version = ?, evidence_digest = ?, structure_digest = ?
                    WHERE book_source_revision_id = ?
                    """,
                    (parser_version, evidence_digest, structure_digest, revision_id),
                )

    @staticmethod
    def _insert_bookmark_node(connection, revision_id: str, node: dict) -> None:
        connection.execute(
            """
            INSERT INTO outline_nodes(
                outline_node_id, book_source_revision_id, identity_revision,
                parent_id, depth, order_index, kind, title, printed_label_hint,
                start_page, start_y, end_page, end_y, resolution_state,
                physical_revision, confidence, evidence_json, source
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 1, ?, ?, ?)
            """,
            (
                node["outline_node_id"], revision_id, node["parent_id"], node["depth"],
                node["order_index"], node["kind"], node["title"],
                node.get("printed_label_hint"), node.get("start_page"),
                node.get("start_y"),
                "PARTIAL" if node.get("start_page") is not None else "UNRESOLVED",
                node.get("confidence", 1.0),
                json.dumps(node.get("evidence", {}), ensure_ascii=False),
                _BOOKMARK_NODE_SOURCE,
            ),
        )

    def clear_bookmark_import(self, revision_id: str) -> int:
        """删除书签来源大纲；返回删除的节点数（幂等，可重复调用）。"""
        with self.database.connect() as connection:
            _ensure_source_column(connection)
            cursor = connection.execute(
                "DELETE FROM outline_nodes WHERE book_source_revision_id = ? AND source = ?",
                (revision_id, _BOOKMARK_NODE_SOURCE),
            )
            record = connection.execute(
                "SELECT evidence_source FROM outline_bootstrap_records WHERE book_source_revision_id = ?",
                (revision_id,),
            ).fetchone()
            if record is not None and record["evidence_source"] == _BOOKMARK_EVIDENCE_SOURCE:
                connection.execute(
                    "DELETE FROM outline_bootstrap_records WHERE book_source_revision_id = ?",
                    (revision_id,),
                )
            return cursor.rowcount
