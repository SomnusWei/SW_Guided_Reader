"""Durable storage for Assistant (临时解释) conversations.

The Assistant itself keeps its live workspaces in memory only, so a Reader
restart silently drops every unsaved explanation and users re-ask the same
questions. This module persists each completed turn set as a single JSON
payload per conversation root, which is enough to browse and export history
without rebuilding the live tree.

The payload intentionally matches the Assistant public root projection
(`label` / `scope` / `turns` / `nodes`), so both the history dialog and the
Markdown exporter can consume it unchanged.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from reader_service.assistant_history_schema import ASSISTANT_HISTORY_SCHEMA


def serialize_root(root) -> dict:
    """Project an in-memory AssistantRoot into the durable public shape."""
    source = root.created_from
    return {
        "root_id": root.root_id,
        "label": root.label,
        "provider": root.provider,
        "model": root.model,
        "scope": root.scope.public(),
        "created_from": {
            "kind": source.kind.value,
            "selected_text_preview": source.label,
            **({"teaching_lineage": copy.deepcopy(source.teaching_lineage)} if source.teaching_lineage else {}),
            **({"source_provenance": copy.deepcopy(source.source_provenance)} if source.source_provenance else {}),
            **({"visual_region": copy.deepcopy(source.visual_region)} if source.visual_region else {}),
        },
        "turns": [turn.public() for turn in root.turns],
        "nodes": [
            {
                "node_id": node.node_id,
                "parent_node_id": node.parent_node_id,
                "depth": node.depth,
                "label": node.label,
                "turns": [turn.public() for turn in node.turns],
                "error": node.error,
            }
            for node in root.nodes.values()
        ],
    }


class AssistantHistoryService:
    """SQLite-backed, read-only archive of completed Assistant conversations."""

    def __init__(self, database):
        self.database = database
        self._lock = threading.Lock()

    # -- write path (called by AssistantService after each completed turn) --

    def save(self, revision_id: str, root) -> None:
        payload = serialize_root(root)
        self.save_payload(revision_id, payload)

    def save_payload(self, revision_id: str, payload: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        turn_count = len(payload.get("turns") or []) + sum(
            len(node.get("turns") or []) for node in payload.get("nodes") or []
        )
        scope = payload.get("scope") or {}
        page_index = scope.get("pdf_page_index")
        with self._lock:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO assistant_history
                        (id, book_source_revision_id, label, provider, model,
                         page_index, turn_count, created_at, updated_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        label = excluded.label,
                        provider = excluded.provider,
                        model = excluded.model,
                        page_index = excluded.page_index,
                        turn_count = excluded.turn_count,
                        updated_at = excluded.updated_at,
                        payload = excluded.payload
                    """,
                    (
                        str(payload["root_id"]),
                        str(revision_id),
                        str(payload.get("label") or "未命名解释"),
                        payload.get("provider"),
                        payload.get("model"),
                        int(page_index) if isinstance(page_index, int) else None,
                        int(turn_count),
                        now,
                        now,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )

    # -- read path ----------------------------------------------------------

    def list(self, revision_id: str) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, label, provider, model, page_index, turn_count,
                       created_at, updated_at
                FROM assistant_history
                WHERE book_source_revision_id = ?
                ORDER BY updated_at DESC, rowid DESC
                """,
                (str(revision_id),),
            ).fetchall()
        return [
            {
                "root_id": row["id"],
                "label": row["label"],
                "provider": row["provider"],
                "model": row["model"],
                "pdf_page_index": row["page_index"],
                "turn_count": row["turn_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get(self, root_id: str) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM assistant_history WHERE id = ?",
                (str(root_id),),
            ).fetchone()
        if row is None:
            raise LookupError("这条 AI 对话记录不存在或已被删除。")
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError) as exc:
            raise LookupError("这条 AI 对话记录数据已损坏。") from exc
        if not isinstance(payload, dict):
            raise LookupError("这条 AI 对话记录数据已损坏。")
        return payload

    def delete(self, root_id: str) -> None:
        with self._lock:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM assistant_history WHERE id = ?", (str(root_id),)
                )
                if cursor.rowcount == 0:
                    raise LookupError("这条 AI 对话记录不存在或已被删除。")


def purge_for_revision(connection: sqlite3.Connection, revision_id: str) -> None:
    """Cascade helper (defensive): history rows die with their book via FK."""
    connection.execute(
        "DELETE FROM assistant_history WHERE book_source_revision_id = ?",
        (str(revision_id),),
    )


__all__ = [
    "ASSISTANT_HISTORY_SCHEMA",
    "AssistantHistoryService",
    "purge_for_revision",
    "serialize_root",
]
