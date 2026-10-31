# -*- coding: utf-8 -*-
"""Markdown 笔记：迁移 32 + NotesService + HTTP 接口。"""

from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from reader_service.library.database import Database
from reader_service.notes import NotesService

REVISION_ID = "2b1c3d4e-5f60-4718-9a2b-3456789abcde"


@pytest.fixture()
def database(tmp_path):
    instance = Database(tmp_path / "state.sqlite3")
    instance.initialize()
    with instance.connect() as connection:
        book_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO books (id, title, created_at) VALUES (?, ?, '2026-01-01T00:00:00')",
            (book_id, "中医基础理论"),
        )
        connection.execute(
            """
            INSERT INTO book_source_revisions (
                id, book_id, blob_sha256, byte_size, page_count,
                page_geometry_json, label, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '2026-01-01T00:00:00')
            """,
            (REVISION_ID, book_id, "a" * 64, 1024, 40, "[]", "v1"),
        )
    return instance


@pytest.fixture()
def notes(database):
    return NotesService(database)


def test_migration_creates_table_and_is_idempotent(database):
    with database.connect() as connection:
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        assert 32 in versions
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='notes'"
        ).fetchone()
        assert table is not None
        assert "book_title_snapshot" in table[0]

    store = NotesService(database)
    created = store.create(title="迁移检查", body="第一行")
    # 再次初始化（升级/重启路径）不会丢掉已有笔记
    database.initialize()
    assert store.get(created["id"])["title"] == "迁移检查"


def test_create_derives_title_and_excerpt_from_body(notes):
    note = notes.create(body="# 生脉散\n\n人参、麦冬、五味子\n\n第二段正文")
    assert note["title"] == "生脉散"
    assert note["excerpt"] == "生脉散"
    assert note["pinned"] is False
    assert note["created_at"] == note["updated_at"]

    named = notes.create(title="  自定标题  ", body="正文")
    assert named["title"] == "自定标题"

    blank = notes.create(title="", body="- [ ] 任务\n- [x] 已完成")
    assert blank["title"] == "任务"

    empty = notes.create()
    assert empty["title"] == "未命名笔记"
    assert empty["body"] == ""


def test_update_only_touches_given_fields(notes):
    note = notes.create(title="原标题", body="原正文")
    updated = notes.update(note["id"], body="# 新正文\n细节")
    assert updated["title"] == "原标题"          # 标题显式写过，不被正文覆盖
    assert updated["body"] == "# 新正文\n细节"
    assert updated["excerpt"] == "新正文"
    assert updated["updated_at"] >= note["updated_at"]
    assert updated["created_at"] == note["created_at"]

    pinned = notes.update(note["id"], pinned=True)
    assert pinned["pinned"] is True
    assert pinned["body"] == updated["body"]

    with pytest.raises(ValueError):
        notes.update(note["id"])
    with pytest.raises(LookupError):
        notes.update(str(uuid.uuid4()), body="x")


def test_title_follows_body_while_still_automatic(notes):
    note = notes.create(body="旧的首行")
    assert note["title"] == "旧的首行"
    renamed = notes.update(note["id"], body="新的首行\n正文")
    assert renamed["title"] == "新的首行"

    fixed = notes.update(note["id"], title="定稿标题")
    assert fixed["title"] == "定稿标题"
    later = notes.update(note["id"], body="换了首行\n正文")
    assert later["title"] == "定稿标题"          # 用户改过标题后不再自动跟随


def test_validation_limits(notes):
    with pytest.raises(ValueError):
        notes.create(body="x" * 200_001)
    with pytest.raises(ValueError):
        notes.create(body=123)
    with pytest.raises(ValueError):
        notes.create(page_index=-1)
    with pytest.raises(ValueError):
        notes.create(page_index=1.5)
    with pytest.raises(ValueError):
        notes.create(book_source_revision_id=str(uuid.uuid4()))
    # 创建时可以直接置顶（接口允许），非法值仍然拒绝
    assert notes.create(body="ok", pinned=True)["pinned"] is True
    with pytest.raises(ValueError):
        notes.create(body="ok", pinned="yes")

    note = notes.create(body="ok")
    with pytest.raises(ValueError):
        notes.update(note["id"], pinned="yes")


def test_list_search_order_and_paging(notes):
    first = notes.create(title="总线事务", body="计组第三章")
    second = notes.create(title="生脉散", body="人参 麦冬")
    third = notes.create(title="错题本", body="总线时序容易错")

    listed = notes.list_notes()
    assert listed["total"] == 3
    assert [item["id"] for item in listed["notes"]][0] == third["id"]  # updated_at 倒序

    assert notes.list_notes(query="总线")["total"] == 2
    assert notes.list_notes(query="麦冬")["total"] == 1
    assert notes.list_notes(query="不存在的词")["total"] == 0

    notes.update(first["id"], pinned=True)
    pinned_first = notes.list_notes()
    assert pinned_first["notes"][0]["id"] == first["id"]
    assert pinned_first["notes"][0]["pinned"] is True

    page = notes.list_notes(limit=2, offset=1)
    assert page["total"] == 3
    assert len(page["notes"]) == 2

    for item in listed["notes"]:
        assert "body" not in item            # 列表只返回摘要，不返回全文
        assert item["excerpt"] is not None


def test_book_reference_survives_book_deletion(notes, database):
    note = notes.create(
        title="第一章要点",
        body="内容",
        book_source_revision_id=REVISION_ID,
        page_index=41,
    )
    assert note["book_source_revision_id"] == REVISION_ID
    assert note["book_title_snapshot"] == "中医基础理论"
    assert note["page_index"] == 41

    assert notes.list_notes(book=REVISION_ID)["total"] == 1
    assert notes.list_notes(book=str(uuid.uuid4()))["total"] == 0

    with database.connect() as connection:
        connection.execute("DELETE FROM book_source_revisions WHERE id = ?", (REVISION_ID,))

    kept = notes.get(note["id"])
    assert kept is not None
    assert kept["book_source_revision_id"] is None     # ON DELETE SET NULL
    assert kept["book_title_snapshot"] == "中医基础理论"  # 书名快照仍可追溯

    cleared = notes.update(note["id"], book_source_revision_id=None, page_index=None)
    assert cleared["book_title_snapshot"] is None
    assert cleared["page_index"] is None


def test_delete_removes_note(notes):
    note = notes.create(title="待删", body="x")
    notes.delete(note["id"])
    with pytest.raises(LookupError):
        notes.get(note["id"])
    with pytest.raises(LookupError):
        notes.delete(note["id"])
    assert notes.list_notes()["total"] == 0


def test_export_single_and_all(notes):
    note = notes.create(
        title="生脉散/方义",
        body="# 生脉散\n\n- 人参\n- 麦冬",
        book_source_revision_id=REVISION_ID,
        page_index=41,
    )
    content, filename = notes.export_one(note["id"])
    assert content.startswith("# 生脉散/方义")
    assert "中医基础理论" in content
    assert "第 42 页" in content
    assert "# 生脉散\n\n- 人参\n- 麦冬" in content      # 正文原样写入
    assert filename.startswith("笔记_生脉散_方义_")     # 非法字符被替换
    assert filename.endswith(".md")
    assert "/" not in filename

    notes.create(title="第二条", body="正文二")
    merged, merged_name = notes.export_all()
    assert merged.startswith("# 笔记本")
    assert "共 2 条笔记" in merged
    assert "## 生脉散/方义" in merged
    assert "## 第二条" in merged
    assert merged_name.endswith(".md") and merged_name.startswith("笔记本_")


def test_http_notes_crud(database):
    from test_api import request_json, running_server

    store = NotesService(database)
    with running_server(database_service(database), notes=store) as (base, token):
        status, created = request_json(
            f"{base}/api/notes",
            token,
            method="POST",
            data=json.dumps({"title": "接口写入", "body": "第一行"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 201
        note_id = created["note"]["id"]

        status, listed = request_json(f"{base}/api/notes?query={quote('接口')}", token)
        assert status == 200
        assert listed["total"] == 1

        status, detail = request_json(f"{base}/api/notes/{note_id}", token)
        assert detail["note"]["body"] == "第一行"

        status, patched = request_json(
            f"{base}/api/notes/{note_id}",
            token,
            method="PATCH",
            data=json.dumps({"body": "改过的正文", "pinned": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert patched["note"]["body"] == "改过的正文"
        assert patched["note"]["pinned"] is True

        with urlopen(Request(
            f"{base}/api/notes/{quote(note_id)}/export",
            headers={"X-Reader-Token": token},
        )) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/markdown")
            disposition = response.headers["Content-Disposition"]
            assert "filename*=UTF-8''" in disposition
            text = response.read().decode("utf-8")
        assert "改过的正文" in text

        with urlopen(Request(
            f"{base}/api/notes/export", headers={"X-Reader-Token": token}
        )) as response:
            assert response.status == 200
            assert "# 笔记本" in response.read().decode("utf-8")

        with urlopen(Request(
            f"{base}/api/notes/{note_id}", headers={"X-Reader-Token": token}, method="DELETE"
        )) as response:
            assert response.status == 204

        with pytest.raises(HTTPError) as not_found:
            request_json(f"{base}/api/notes/{note_id}", token)
        assert not_found.value.code == 404

        with pytest.raises(HTTPError) as bad:
            request_json(
                f"{base}/api/notes",
                token,
                method="POST",
                data=json.dumps({"unknown": 1}).encode(),
                headers={"Content-Type": "application/json"},
            )
        assert bad.value.code == 400


def database_service(database):
    """HTTP 层只用到 ``service.database``；其余依赖保持惰性。"""

    class _Service:
        def __init__(self, db):
            self.database = db

    return _Service(database)
