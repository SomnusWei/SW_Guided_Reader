"""Markdown 笔记表结构（迁移 32）。

独立成模块的原因与 ``assistant_history_schema.py`` 一致：让
``library.database`` 的迁移机制可以零依赖导入，避免在数据库初始化期间
连带引入业务模块造成循环导入。
"""

NOTES_SCHEMA = """
CREATE TABLE notes (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL DEFAULT '未命名笔记',
    body                    TEXT NOT NULL DEFAULT '',
    excerpt                 TEXT NOT NULL DEFAULT '',
    book_source_revision_id TEXT REFERENCES book_source_revisions(id) ON DELETE SET NULL,
    page_index              INTEGER,
    book_title_snapshot     TEXT,
    pinned                  INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX notes_updated_idx ON notes(updated_at DESC);
CREATE INDEX notes_book_idx ON notes(book_source_revision_id);
"""

__all__ = ["NOTES_SCHEMA"]
