# -*- coding: utf-8 -*-
"""AssistantHistoryService: 每轮问答后的本地归档（迁移 29 的 assistant_history 表）。"""

from __future__ import annotations

import json
import uuid

import pytest

from reader_service.assistant.history import AssistantHistoryService
from reader_service.library.database import Database

REVISION_ID = "1f0a2b3c-4d5e-4f60-8a91-23456789abcd"


@pytest.fixture()
def database(tmp_path):
    instance = Database(tmp_path / "state.sqlite3")
    instance.initialize()
    with instance.connect() as connection:
        connection.execute(
            "INSERT INTO books (id, title, created_at) VALUES (?, ?, '2026-01-01T00:00:00')",
            (str(uuid.uuid4()), "测试教材"),
        )
        connection.execute(
            """
            INSERT INTO book_source_revisions (
                id, book_id, blob_sha256, byte_size, page_count,
                page_geometry_json, label, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '2026-01-01T00:00:00')
            """,
            (
                REVISION_ID,
                connection.execute("SELECT id FROM books").fetchone()[0],
                "a" * 64,
                1024,
                10,
                "[]",
                "v1",
            ),
        )
    return instance


def payload(root_id: str) -> dict:
    return {
        "root_id": root_id,
        "label": "王肯堂《证治准绳》",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "scope": {"pdf_page_index": 3, "chapter_title": "第一章"},
        "created_from": {"kind": "ORIGINAL_PDF", "selected_text_preview": "王肯堂"},
        "turns": [{"turn_id": "t1", "question": "这是什么？", "answer": "这是一条注解。"}],
        "nodes": [
            {
                "node_id": "n1",
                "parent_node_id": None,
                "depth": 2,
                "label": "失笑散",
                "turns": [{"turn_id": "t2", "question": "组成？", "answer": "蒲黄、五灵脂。"}],
            }
        ],
    }


def test_save_list_get_delete_roundtrip(database):
    store = AssistantHistoryService(database)
    root_id = str(uuid.uuid4())
    store.save_payload(REVISION_ID, payload(root_id))

    listed = store.list(REVISION_ID)
    assert len(listed) == 1
    entry = listed[0]
    assert entry["root_id"] == root_id
    assert entry["label"] == "王肯堂《证治准绳》"
    assert entry["turn_count"] == 2  # 根 1 轮 + 子节点 1 轮
    assert entry["pdf_page_index"] == 3

    detail = store.get(root_id)
    assert detail["turns"][0]["answer"] == "这是一条注解。"
    assert detail["nodes"][0]["label"] == "失笑散"

    store.delete(root_id)
    assert store.list(REVISION_ID) == []
    with pytest.raises(LookupError):
        store.get(root_id)


def test_upsert_updates_existing_row(database):
    store = AssistantHistoryService(database)
    root_id = str(uuid.uuid4())
    store.save_payload(REVISION_ID, payload(root_id))
    updated = payload(root_id)
    updated["turns"].append({"turn_id": "t3", "question": "再解释一下？", "answer": "好的。"})
    store.save_payload(REVISION_ID, updated)

    listed = store.list(REVISION_ID)
    assert len(listed) == 1
    assert listed[0]["turn_count"] == 3
    assert "再解释一下？" in json.dumps(store.get(root_id), ensure_ascii=False)


def test_history_is_scoped_per_revision_and_cascades_on_book_delete(database):
    store = AssistantHistoryService(database)
    other_revision = str(uuid.uuid4())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO book_source_revisions (
                id, book_id, blob_sha256, byte_size, page_count,
                page_geometry_json, label, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'v1', 'SUPERSEDED', '2026-01-01T00:00:00')
            """.replace("?, ?, ?, ?, ?, ?",
                        "?, (SELECT id FROM books), ?, ?, ?, ?"),
            (other_revision, "b" * 64, 1024, 10, "[]"),
        )
    root_id = str(uuid.uuid4())
    store.save_payload(REVISION_ID, payload(root_id))

    assert store.list(other_revision) == []
    assert len(store.list(REVISION_ID)) == 1

    with database.connect() as connection:
        connection.execute(
            "DELETE FROM book_source_revisions WHERE id = ?", (REVISION_ID,)
        )
    assert store.list(REVISION_ID) == []
    with pytest.raises(LookupError):
        store.get(root_id)
