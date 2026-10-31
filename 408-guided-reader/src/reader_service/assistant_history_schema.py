"""Assistant 对话归档表结构（迁移 29）。

独立成模块是为了让 ``library.database`` 的迁移机制可以零依赖导入：
``reader_service.assistant`` 包的 ``__init__`` 会连带引入 foundation 等重模块，
在 database 初始化期间会造成循环导入。
"""

ASSISTANT_HISTORY_SCHEMA = """
CREATE TABLE assistant_history (
    id TEXT PRIMARY KEY,
    book_source_revision_id TEXT NOT NULL REFERENCES book_source_revisions(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    page_index INTEGER,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX assistant_history_revision_idx
    ON assistant_history(book_source_revision_id, updated_at DESC);
"""

__all__ = ["ASSISTANT_HISTORY_SCHEMA"]
