from __future__ import annotations

from io import BytesIO

import pytest

from reader_service.annotation import AnnotationRepository, AnnotationService
from reader_service.foundation import FoundationRepository, FoundationService

from conftest import make_pdf
from test_annotations import LINES, UnusedEngine, prepared_services  # noqa: F401  (helpers)


def projection(revision_id, *, question="什么是总线事务？", answer="总线事务由地址、数据与控制信号组成。"):
    return {
        "revision_id": revision_id,
        "pdf_page_index": 0,
        "source": {
            "quads": [[[0.2, 0.1], [0.6, 0.1], [0.6, 0.15], [0.2, 0.15]]],
            "quote": "乙丙丁",
            "context_before": "甲",
            "context_after": "戊",
            "foundation_version": 1,
        },
        "provenance": {
            "root_focus": "总线事务",
            "child_focus": None,
            "concept_path": ["总线事务"],
            "answer_question": question,
        },
        "source_grounding": {
            "pdf_page_number": 1,
            "printed_page_label": "1",
            "chapter_title": "第一章 总线",
            "section_title": "1.1 总线事务",
            "bounded_same_page_ocr": "总线事务的上下文",
        },
        "ai_content": answer,
    }


def create_all_kinds(annotations, revision_id):
    selection = {"start": {"line_ordinal": 0, "boundary": 1}, "end": {"line_ordinal": 1, "boundary": 2}}
    highlight = annotations.create(
        revision_id, kind="HIGHLIGHT", page_index=0, highlight_style="YELLOW", **selection
    )
    underline = annotations.create(
        revision_id, kind="UNDERLINE", page_index=0, highlight_style="GREEN", body="强调", **selection
    )
    textbox = annotations.create(
        revision_id,
        kind="TEXTBOX",
        page_index=0,
        rect=[0.1, 0.3, 0.5, 0.4],
        body="文本框笔记",
        highlight_style="BLUE",
    )
    region = annotations.create(
        revision_id,
        kind="REGION",
        page_index=0,
        rect=[0.2, 0.5, 0.8, 0.9],
        body="区域说明",
        highlight_style="RED",
    )
    return highlight, underline, textbox, region


def test_create_five_kinds_persist_kind_specific_geometry(service):
    _, revision, annotations = prepared_services(service)
    highlight, underline, textbox, region = create_all_kinds(annotations, revision["id"])
    ai_note, created = annotations.create_ai_saved(
        revision["id"], save_intent_id="ext-save-intent-1", projection=projection(revision["id"])
    )
    assert created is True

    assert highlight["kind"] == "HIGHLIGHT"
    assert highlight["quads"] and highlight["rect"] is None
    assert highlight["foundation_version_at_creation"] == 1
    assert highlight["quote"] == "乙丙丁戊\n己庚"

    assert underline["kind"] == "UNDERLINE"
    assert underline["quads"] and underline["body"] == "强调"

    assert textbox["kind"] == "TEXTBOX"
    assert textbox["rect"] == [0.1, 0.3, 0.5, 0.4]
    assert textbox["quads"] is None
    assert textbox["quote"] is None
    assert textbox["foundation_version_at_creation"] is None

    assert region["kind"] == "REGION"
    assert region["highlight_style"] == "RED"
    assert region["payload"] is None

    assert ai_note["kind"] == "AI_NOTE"
    assert ai_note["source_kind"] == "AI_SAVED"
    assert ai_note["highlight_style"] == "NONE"
    assert ai_note["body"] == "> 什么是总线事务？\n\n总线事务由地址、数据与控制信号组成。"
    assert ai_note["payload"] == {
        "question": "什么是总线事务？",
        "answer": "总线事务由地址、数据与控制信号组成。",
        "quote": "乙丙丁",
        "page_index": 0,
    }

    page_items = annotations.list_page(revision["id"], 0)
    assert [value["kind"] for value in page_items] == [
        "HIGHLIGHT", "UNDERLINE", "TEXTBOX", "REGION", "AI_NOTE"
    ]


@pytest.mark.parametrize(
    "rect",
    (
        [0.5, 0.5, 0.4, 0.6],
        [0.1, 0.6, 0.4, 0.5],
        [-0.1, 0.1, 0.5, 0.2],
        [0.1, 0.1, 1.2, 0.2],
        [0.1, 0.1, 0.5],
        [0.1, 0.1, 0.5, "0.2"],
        [True, 0.1, 0.5, 0.2],
    ),
)
def test_rect_annotations_reject_invalid_rectangles(service, rect):
    _, revision, annotations = prepared_services(service)
    with pytest.raises(ValueError):
        annotations.create(
            revision["id"], kind="REGION", page_index=0, rect=rect, highlight_style="RED"
        )
    assert annotations.list_page(revision["id"], 0) == []


def test_rect_annotation_rejects_page_out_of_range(service):
    _, revision, annotations = prepared_services(service)
    with pytest.raises(ValueError, match="Page index"):
        annotations.create(
            revision["id"], kind="TEXTBOX", page_index=9, rect=[0.1, 0.1, 0.3, 0.3]
        )


def test_anchored_kind_requires_selection_and_unknown_kind_is_rejected(service):
    _, revision, annotations = prepared_services(service)
    with pytest.raises(ValueError, match="selection"):
        annotations.create(revision["id"], kind="HIGHLIGHT", page_index=0)
    with pytest.raises(ValueError, match="kind is invalid"):
        annotations.create(
            revision["id"], kind="STICKER", page_index=0, rect=[0.1, 0.1, 0.2, 0.2]
        )


def test_partial_update_body_color_and_rect(service):
    _, revision, annotations = prepared_services(service)
    _, _, textbox, _ = create_all_kinds(annotations, revision["id"])

    updated = annotations.update(
        revision["id"], textbox["id"], body="  新的说明  ", color="purple"
    )
    assert updated["body"] == "新的说明"
    assert updated["highlight_style"] == "PURPLE"
    assert updated["rect"] == textbox["rect"]

    moved = annotations.update(revision["id"], textbox["id"], rect=[0.3, 0.4, 0.6, 0.7])
    assert moved["rect"] == [0.3, 0.4, 0.6, 0.7]

    with pytest.raises(ValueError):
        annotations.update(revision["id"], moved["id"], rect=[0.5, 0.5, 0.4, 0.6])
    with pytest.raises(ValueError, match="Highlight style"):
        annotations.update(revision["id"], moved["id"], color="PINK")
    assert annotations.get(revision["id"], moved["id"])["highlight_style"] == "PURPLE"


def test_anchored_and_ai_annotations_cannot_move_and_ai_notes_are_immutable(service):
    _, revision, annotations = prepared_services(service)
    highlight = create_all_kinds(annotations, revision["id"])[0]
    with pytest.raises(ValueError, match="Only textbox and region"):
        annotations.update(revision["id"], highlight["id"], rect=[0.1, 0.1, 0.2, 0.2])

    ai_note, _ = annotations.create_ai_saved(
        revision["id"], save_intent_id="ext-save-intent-update", projection=projection(revision["id"])
    )
    with pytest.raises(ValueError, match="cannot be edited"):
        annotations.update(revision["id"], ai_note["id"], body="rewrite")


def test_delete_and_list_all_orders_by_page_then_creation(service):
    payload = make_pdf(((612, 792), (612, 792)))
    revision = service.intake(
        BytesIO(payload), content_length=len(payload), filename="two-pages.pdf"
    )["book"]["active_revision"]
    annotations = AnnotationService(
        FoundationService(service, FoundationRepository(service.database), UnusedEngine),
        AnnotationRepository(service.database),
    )
    first = annotations.create(
        revision["id"], kind="TEXTBOX", page_index=0, rect=[0.1, 0.1, 0.4, 0.2], body="甲"
    )
    second = annotations.create(
        revision["id"], kind="REGION", page_index=0, rect=[0.1, 0.3, 0.4, 0.4]
    )
    third = annotations.create(
        revision["id"], kind="REGION", page_index=1, rect=[0.1, 0.1, 0.4, 0.4]
    )
    ordered = annotations.list_all(revision["id"])
    assert [value["id"] for value in ordered] == [first["id"], second["id"], third["id"]]
    assert [value["pdf_page_index"] for value in ordered] == [0, 0, 1]

    annotations.delete(revision["id"], second["id"])
    remaining = [value["id"] for value in annotations.list_all(revision["id"])]
    assert remaining == [first["id"], third["id"]]
    with pytest.raises(LookupError, match="Annotation not found"):
        annotations.delete(revision["id"], second["id"])


def test_ai_note_save_intent_is_idempotent_and_keeps_markdown_payload(service):
    _, revision, annotations = prepared_services(service)
    first, created = annotations.create_ai_saved(
        revision["id"], save_intent_id="ext-save-intent-idem", projection=projection(revision["id"])
    )
    second, created_again = annotations.create_ai_saved(
        revision["id"], save_intent_id="ext-save-intent-idem", projection=projection(revision["id"])
    )
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]

    stored = annotations.find_by_save_intent(revision["id"], "ext-save-intent-idem")
    assert stored["id"] == first["id"]
    assert stored["body"].startswith("> 什么是总线事务？\n\n")
    assert set(stored["payload"]) == {"question", "answer", "quote", "page_index"}
    assert len(annotations.list_page(revision["id"], 0)) == 1


def test_ai_note_question_is_flattened_into_one_quote_line(service):
    _, revision, annotations = prepared_services(service)
    multiline = projection(revision["id"], question="第一行\n第二行", answer="回答")
    ai_note, _ = annotations.create_ai_saved(
        revision["id"], save_intent_id="ext-save-intent-multiline", projection=multiline
    )
    assert ai_note["body"] == "> 第一行 第二行\n\n回答"
    assert ai_note["payload"]["question"] == "第一行\n第二行"


def test_ai_note_requires_matching_revision_and_completed_answer(service):
    _, revision, annotations = prepared_services(service)
    wrong_revision = projection(revision["id"])
    wrong_revision["revision_id"] = "not-this-revision"
    with pytest.raises(ValueError, match="does not match"):
        annotations.create_ai_saved(
            revision["id"], save_intent_id="ext-save-intent-mismatch", projection=wrong_revision
        )

    empty = projection(revision["id"], answer="   ")
    with pytest.raises(ValueError, match="completed Assistant answer"):
        annotations.create_ai_saved(
            revision["id"], save_intent_id="ext-save-intent-empty", projection=empty
        )


def test_migration_28_maps_legacy_kinds_and_backfills_ai_payload(tmp_path):
    import json
    import sqlite3

    from reader_service.library.database import Database, MIGRATIONS

    path = tmp_path / "pre28.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    for version, sql in MIGRATIONS:
        if version >= 28:
            break
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    connection.execute(
        "INSERT INTO books(id, title, status, created_at) "
        "VALUES ('book', '迁移', 'ACTIVE', 'book-created')"
    )
    connection.execute(
        """
        INSERT INTO book_source_revisions(
            id, book_id, blob_sha256, byte_size, page_count, page_geometry_json,
            label, status, created_at, foundation_version
        ) VALUES ('revision', 'book', ?, 1, 1, '[]', '迁移', 'ACTIVE', 'rev-created', 1)
        """,
        ("d" * 64,),
    )
    connection.execute(
        """
        INSERT INTO annotations(
            id, book_source_revision_id, pdf_page_index, kind, quads_json, quote,
            context_before, context_after, foundation_version_at_creation, body,
            highlight_style, source_kind, verification_state, anchor_state,
            knowledge_point_id, created_at
        ) VALUES ('user-mark', 'revision', 0, 'TEXT',
                  '[[[0.1,0.1],[0.5,0.1],[0.5,0.2],[0.1,0.2]]]', '旧选区', '', '', 1,
                  '旧笔记', 'GREEN', 'USER', NULL, 'OK', NULL, 'a1')
        """
    )
    connection.execute(
        """
        INSERT INTO annotations(
            id, book_source_revision_id, pdf_page_index, kind, quads_json, quote,
            context_before, context_after, foundation_version_at_creation, body,
            highlight_style, source_kind, verification_state, anchor_state,
            knowledge_point_id, save_intent_id, provenance_json,
            source_grounding_json, created_at
        ) VALUES ('ai-mark', 'revision', 0, 'TEXT',
                  '[[[0.1,0.3],[0.5,0.3],[0.5,0.4],[0.1,0.4]]]', '旧解释', '', '', 1,
                  '旧回答正文', 'NONE', 'AI_SAVED', 'PASS', 'OK', NULL,
                  'legacy-save-intent',
                  '{"root_focus":"总线","child_focus":null,"concept_path":["总线"],"answer_question":"什么是总线？"}',
                  '{"pdf_page_number":1,"printed_page_label":"1","chapter_title":"","section_title":"","bounded_same_page_ocr":""}',
                  'a2')
        """
    )
    connection.commit()
    connection.close()

    Database(path).initialize()

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    rows = {row["id"]: row for row in connection.execute("SELECT * FROM annotations")}
    assert rows["user-mark"]["kind"] == "HIGHLIGHT"
    assert rows["user-mark"]["payload_json"] is None
    assert rows["ai-mark"]["kind"] == "AI_NOTE"
    payload = json.loads(rows["ai-mark"]["payload_json"])
    assert payload == {
        "question": "什么是总线？",
        "answer": "旧回答正文",
        "quote": "旧解释",
        "page_index": 0,
    }
    triggers = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert {"memory_annotation_deleted", "memory_eligible_source"} <= triggers
    connection.close()


def test_migration_28_maps_legacy_kinds_and_backfills_ai_payload(tmp_path):
    import json
    import sqlite3

    from reader_service.library.database import Database, MIGRATIONS

    path = tmp_path / "pre28.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    for version, sql in MIGRATIONS:
        if version >= 28:
            break
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    connection.execute(
        "INSERT INTO books(id, title, status, created_at) "
        "VALUES ('book', '迁移', 'ACTIVE', 'book-created')"
    )
    connection.execute(
        """
        INSERT INTO book_source_revisions(
            id, book_id, blob_sha256, byte_size, page_count, page_geometry_json,
            label, status, created_at, foundation_version
        ) VALUES ('revision', 'book', ?, 1, 1, '[]', '迁移', 'ACTIVE', 'rev-created', 1)
        """,
        ("d" * 64,),
    )
    connection.execute(
        """
        INSERT INTO annotations(
            id, book_source_revision_id, pdf_page_index, kind, quads_json, quote,
            context_before, context_after, foundation_version_at_creation, body,
            highlight_style, source_kind, verification_state, anchor_state,
            knowledge_point_id, created_at
        ) VALUES ('user-mark', 'revision', 0, 'TEXT',
                  '[[[0.1,0.1],[0.5,0.1],[0.5,0.2],[0.1,0.2]]]', '旧选区', '', '', 1,
                  '旧笔记', 'GREEN', 'USER', NULL, 'OK', NULL, 'a1')
        """
    )
    connection.execute(
        """
        INSERT INTO annotations(
            id, book_source_revision_id, pdf_page_index, kind, quads_json, quote,
            context_before, context_after, foundation_version_at_creation, body,
            highlight_style, source_kind, verification_state, anchor_state,
            knowledge_point_id, save_intent_id, provenance_json,
            source_grounding_json, created_at
        ) VALUES ('ai-mark', 'revision', 0, 'TEXT',
                  '[[[0.1,0.3],[0.5,0.3],[0.5,0.4],[0.1,0.4]]]', '旧解释', '', '', 1,
                  '旧回答正文', 'NONE', 'AI_SAVED', 'PASS', 'OK', NULL,
                  'legacy-save-intent',
                  '{"root_focus":"总线","child_focus":null,"concept_path":["总线"],"answer_question":"什么是总线？"}',
                  '{"pdf_page_number":1,"printed_page_label":"1","chapter_title":"","section_title":"","bounded_same_page_ocr":""}',
                  'a2')
        """
    )
    connection.commit()
    connection.close()

    Database(path).initialize()

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    rows = {row["id"]: row for row in connection.execute("SELECT * FROM annotations")}
    assert rows["user-mark"]["kind"] == "HIGHLIGHT"
    assert rows["user-mark"]["payload_json"] is None
    assert rows["ai-mark"]["kind"] == "AI_NOTE"
    payload = json.loads(rows["ai-mark"]["payload_json"])
    assert payload == {
        "question": "什么是总线？",
        "answer": "旧回答正文",
        "quote": "旧解释",
        "page_index": 0,
    }
    triggers = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert {"memory_annotation_deleted", "memory_eligible_source"} <= triggers
    connection.close()


def test_create_note_supports_move_and_body(service):
    _, revision, annotations = prepared_services(service)
    note = annotations.create(
        revision["id"],
        kind="NOTE",
        page_index=0,
        rect=[0.15, 0.2, 0.35, 0.32],
        body="这一段要背",
        highlight_style="YELLOW",
    )
    assert note["kind"] == "NOTE"
    assert note["rect"] == [0.15, 0.2, 0.35, 0.32]
    assert note["quads"] is None
    assert note["quote"] is None
    assert note["foundation_version_at_creation"] is None

    moved = annotations.update(revision["id"], note["id"], rect=[0.2, 0.25, 0.4, 0.37])
    assert moved["rect"] == [0.2, 0.25, 0.4, 0.37]
    retitled = annotations.update(revision["id"], note["id"], body="改内容")
    assert retitled["body"] == "改内容"

    page_items = annotations.list_page(revision["id"], 0)
    assert [value["kind"] for value in page_items] == ["NOTE"]


def test_migration_30_keeps_rows_and_allows_note_kind(tmp_path):
    import sqlite3

    from reader_service.library.database import Database, MIGRATIONS

    path = tmp_path / "pre30.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    for version, sql in MIGRATIONS:
        if version >= 30:
            break
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    connection.execute(
        "INSERT INTO books(id, title, status, created_at) "
        "VALUES ('book', '迁移', 'ACTIVE', 'book-created')"
    )
    connection.execute(
        """
        INSERT INTO book_source_revisions(
            id, book_id, blob_sha256, byte_size, page_count, page_geometry_json,
            label, status, created_at, foundation_version
        ) VALUES ('revision', 'book', ?, 1, 1, '[]', '迁移', 'ACTIVE', 'rev-created', 1)
        """,
        ("d" * 64,),
    )
    connection.execute(
        """
        INSERT INTO annotations(
            id, book_source_revision_id, pdf_page_index, kind, rect_json,
            highlight_style, source_kind, anchor_state, created_at
        ) VALUES ('keep-textbox', 'revision', 0, 'TEXTBOX',
                  '[0.1,0.2,0.3,0.4]', 'BLUE', 'USER', 'OK', 'a1')
        """
    )
    connection.commit()
    connection.close()

    Database(path).initialize()

    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT id, kind, rect_json FROM annotations ORDER BY id"
    ).fetchall()
    assert rows == [("keep-textbox", "TEXTBOX", "[0.1,0.2,0.3,0.4]")]
    connection.execute(
        """
        INSERT INTO annotations(
            id, book_source_revision_id, pdf_page_index, kind, rect_json,
            highlight_style, source_kind, anchor_state, created_at
        ) VALUES ('new-note', 'revision', 0, 'NOTE',
                  '[0.2,0.2,0.4,0.3]', 'YELLOW', 'USER', 'OK', 'a2')
        """
    )
    connection.commit()
    kinds = [row[0] for row in connection.execute("SELECT kind FROM annotations ORDER BY created_at")]
    assert kinds == ["TEXTBOX", "NOTE"]
    connection.close()


def test_custom_hex_color_create_update_and_reject(service):
    """自定义颜色 #RRGGBB：创建与更新都统一存成 #RRGGBB，非法值拒绝。"""
    _, revision, annotations = prepared_services(service)
    selection = {"start": {"line_ordinal": 0, "boundary": 1}, "end": {"line_ordinal": 1, "boundary": 2}}
    created = annotations.create(
        revision["id"],
        kind="HIGHLIGHT",
        page_index=0,
        highlight_style="#33ccff",
        **selection,
    )
    assert created["highlight_style"] == "#33CCFF"

    updated = annotations.update(revision["id"], created["id"], color="12ab56")
    assert updated["highlight_style"] == "#12AB56"

    # 保留枚举名行为不变
    enum_back = annotations.update(revision["id"], created["id"], color="purple")
    assert enum_back["highlight_style"] == "PURPLE"

    with pytest.raises(ValueError):
        annotations.update(revision["id"], created["id"], color="#12345")
    with pytest.raises(ValueError):
        annotations.update(revision["id"], created["id"], color="#12zz56")
    with pytest.raises(ValueError):
        annotations.update(revision["id"], created["id"], color="not-a-color")
