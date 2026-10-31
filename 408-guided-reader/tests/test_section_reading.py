"""阅读进度（2.0 从 learning 包独立到 progress）：只记"读到第几节末尾"，与知识点无关。"""
import sqlite3
from uuid import uuid4

import pytest

from reader_service.library.database import Database
from reader_service.progress import reading


class _Database:
    """最小可用 Database：一个内存库 + migrations 里的 reading 表。"""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE outline_nodes (
                outline_node_id TEXT PRIMARY KEY,
                book_source_revision_id TEXT NOT NULL,
                parent_id TEXT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                order_index INTEGER NOT NULL DEFAULT 0,
                resolution_state TEXT NOT NULL,
                start_page INTEGER,
                start_y REAL,
                end_page INTEGER,
                end_y REAL
            );
            CREATE TABLE book_source_revisions (
                id TEXT PRIMARY KEY,
                page_count INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        self.conn.executescript(reading.SCHEMA)

    def connect(self):
        return _Connection(self.conn)


class _Connection:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        self.conn.commit()
        return False


def fixture():
    database = _Database()
    revision_id = str(uuid4())
    section_id = str(uuid4())
    database.conn.execute(
        "INSERT INTO book_source_revisions VALUES (?, ?, 'ACTIVE')", (revision_id, 10)
    )
    database.conn.execute(
        "INSERT INTO outline_nodes (outline_node_id, book_source_revision_id, parent_id, kind, title,"
        " order_index, resolution_state, start_page, start_y, end_page, end_y)"
        " VALUES (?,?,NULL,'SECTION','1.1 第一节',0,'RESOLVED',1,0.2,2,0.5)",
        (section_id, revision_id),
    )
    return database, revision_id, section_id


def test_targets_expose_section_end_and_ignore_unresolved():
    database, revision_id, section_id = fixture()
    targets = reading.targets(database.conn, revision_id)
    assert [t["outline_node_id"] for t in targets] == [section_id]
    assert targets[0]["pdf_page_index"] == 2
    assert targets[0]["y"] == 0.5


def test_observe_records_once_and_is_idempotent():
    database, revision_id, section_id = fixture()
    first = reading.observe(database, revision_id, 2, 0.4, 0.6)
    assert first["sections"][0]["reading_reached_end_at"] is not None
    record = first["sections"][0]["reading_reached_end_at"]

    second = reading.observe(database, revision_id, 2, 0.4, 0.6)
    assert second["sections"][0]["reading_reached_end_at"] == record


def test_boundary_outside_visible_range_is_not_recorded():
    database, revision_id, _section_id = fixture()
    result = reading.observe(database, revision_id, 2, 0.7, 0.9)
    assert result["sections"][0]["reading_reached_end_at"] is None


def test_observe_rejects_invalid_ranges():
    database, revision_id, _section_id = fixture()
    for page, top, bottom in ((-1, 0.1, 0.2), (10, 0.1, 0.2), (0, 0.5, 0.4), (0, -0.1, 0.2)):
        with pytest.raises((ValueError, LookupError)):
            reading.observe(database, revision_id, page, top, bottom)
