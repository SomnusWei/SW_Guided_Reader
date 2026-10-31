import pytest

from reader_service.annotation import AnnotationRepository, AnnotationService
from reader_service.export_md import (
    MarkdownExporter,
    _annotation_row,
    _sanitize_filename,
)
from reader_service.storage import ManagedPaths

from test_ask_about_this import (
    assistant_fixture,
    create_child_for_text,
    focused,
    selection,
)
from test_saved_explanations import services


def make_exporter(database, data_root):
    return MarkdownExporter(database, ManagedPaths(data_root))


def saved_explanation(assistant_fixture, *, assistant_answers, review_answers, save_intent_id):
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=assistant_answers,
        review_answers=review_answers,
    )
    session_id = f"reader-session-export-{save_intent_id}"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    result = saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=current["root_id"],
        node_id=None,
        turn_id=current["turns"][0]["turn_id"],
        save_intent_id=save_intent_id,
    )
    assert saved.wait_for_idle()
    return assistant, annotations, result["annotation"]


def test_assistant_conversation_export_structure_and_raw_markdown(assistant_fixture):
    assistant, _annotations, _saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("根回答包含**加粗**与`代码`的完整解释", "追问回答的完整内容"),
        review_answers=(),
    )
    revision = assistant_fixture["revision_id"]
    session_id = "reader-session-export-assistant"
    state = assistant.ask_selection(session_id, revision, 3, **selection(3))
    root_id = focused(state)["root_id"]
    assistant.follow_up(session_id, root_id, "再解释一下时钟同步？")
    root = assistant.state(session_id)["roots"][0]

    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )
    md = exporter.export_assistant_conversation(revision, root)

    assert md.startswith("# 《assistant》 · 选段问答")
    assert "**解释焦点**：总线事务" in md
    assert "> 总线事务" in md
    assert "第 4 页" in md
    assert "## 问" in md
    assert "## 答" in md
    assert "### 追问 1" in md
    assert "**问**：再解释一下时钟同步？" in md
    # Raw answer Markdown must be preserved verbatim.
    assert "根回答包含**加粗**与`代码`的完整解释" in md


def test_assistant_child_level_export(assistant_fixture):
    assistant, _annotations, _saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("根回答包含像乐队里的节拍器", "子回答的**完整**正文"),
        review_answers=(),
    )
    revision = assistant_fixture["revision_id"]
    session_id = "reader-session-export-child"
    root_state = assistant.ask_selection(session_id, revision, 3, **selection(3))
    child_state = create_child_for_text(assistant, session_id, root_state, "像乐队里的节拍器")
    child = focused(child_state)
    root = assistant.state(session_id)["roots"][0]

    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )
    md = exporter.export_assistant_conversation(revision, root, node_id=child["node_id"])

    assert "**解释焦点**：像乐队里的节拍器" in md
    assert "子回答的**完整**正文" in md
    assert "根回答包含像乐队里的节拍器" not in md

    full = exporter.export_assistant_conversation(revision, root)
    assert "根回答包含像乐队里的节拍器" in full

    with pytest.raises(LookupError):
        exporter.export_assistant_conversation(revision, root, node_id="missing-node")


def test_saved_explanation_export(assistant_fixture):
    _assistant, annotations, annotation = saved_explanation(
        assistant_fixture,
        assistant_answers=("保存的解释回答正文",),
        review_answers=('{"verdict":"PASS","summary":"在给定依据内可接受。"}',),
        save_intent_id="export-saved-explanation",
    )
    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )
    md = exporter.export_saved_explanation(annotation["id"])

    assert "已保存的解释" in md
    assert "第 4 页" in md
    assert "印刷页码 291" in md
    assert "章节 第6章 总线" in md
    assert "> 总线事务" in md
    assert "## 问" in md
    assert "## 答" in md
    assert "保存的解释回答正文" in md
    assert "AI 审查：AI 审查通过" in md

    repository = AnnotationRepository(assistant_fixture["database"])
    repository.create(
        revision_id=annotation["book_source_revision_id"],
        page_index=0,
        quads=[[[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]]],
        quote="普通用户批注",
        context_before="",
        context_after="",
        foundation_version=1,
        body=None,
        highlight_style="YELLOW",
    )

    with pytest.raises(ValueError):
        exporter.export_saved_explanation(
            annotations.list_page(assistant_fixture["revision_id"], 0)[0]["id"]
        )


def test_annotations_export_sorted_with_ai_qa_and_user_note(assistant_fixture):
    _assistant, annotations, annotation = saved_explanation(
        assistant_fixture,
        assistant_answers=("批注汇总里的 AI 回答",),
        review_answers=('{"verdict":"FAIL","summary":"解释超出教材依据。"}',),
        save_intent_id="export-annotations-ai",
    )
    foundation = assistant_fixture["foundation"]
    foundation_service = foundation
    annotations_service = AnnotationService(
        foundation_service, AnnotationRepository(assistant_fixture["database"])
    )
    annotations_service.create_text(
        assistant_fixture["revision_id"],
        page_index=3,
        start=selection(3)["start"],
        end=selection(3)["end"],
        body="用户的文字笔记内容",
        highlight_style="GREEN",
    )

    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )
    md = exporter.export_annotations(assistant_fixture["revision_id"])

    assert "# 《assistant》 · 批注汇总" in md
    assert "第 4 页" in md
    assert "批注 1（AI 保存解释 · 无底色）" in md
    assert "批注 2（用户批注 · 绿色）" in md
    assert "批注汇总里的 AI 回答" in md
    assert "**问**：" in md and "**答**：" in md
    assert "用户的文字笔记内容" in md
    assert "AI 审查：AI 审查未通过" in md
    saved_row = annotations.get(assistant_fixture["revision_id"], annotation["id"])
    assert saved_row["payload"]["answer"] == "批注汇总里的 AI 回答"
    assert saved_row["body"].startswith("> 总线事务")


def test_bundle_combines_scopes_with_appendix(assistant_fixture):
    assistant, _annotations, _saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("打包导出的回答",),
        review_answers=(),
    )
    revision = assistant_fixture["revision_id"]
    session_id = "reader-session-export-bundle"
    state = assistant.ask_selection(session_id, revision, 3, **selection(3))
    root = state["roots"][0]

    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )
    md = exporter.export_bundle(revision, assistant_roots=[root], include_annotations=True)

    assert "# 《assistant》 · 学习导出" in md
    assert "# 《assistant》 · 选段问答" in md
    assert "打包导出的回答" in md
    assert "# 《assistant》 · 批注汇总" in md
    assert "---" in md

    with pytest.raises(ValueError):
        exporter.export_bundle(revision)


def test_annotation_qa_prefers_payload_and_falls_back_to_legacy_columns():
    base = {
        "id": "a1",
        "book_source_revision_id": "rev",
        "pdf_page_index": 3,
        "kind": "AI_NOTE",
        "quote": "总线事务",
        "body": "legacy body markdown",
        "highlight_style": "NONE",
        "source_kind": "AI_SAVED",
        "verification_state": "PASS",
        "provenance_json": '{"answer_question": "provenance 问"}',
        "source_grounding_json": None,
        "payload_json": '{"question": "payload 问", "answer": "payload 答"}',
        "created_at": "2026-10-03T09:00:00",
    }
    question, answer, quote, page_index = MarkdownExporter._qa_parts(_annotation_row(base))
    assert (question, answer, quote, page_index) == ("payload 问", "payload 答", "总线事务", 3)

    legacy = dict(base, payload_json=None)
    question, answer, _quote, _page = MarkdownExporter._qa_parts(_annotation_row(legacy))
    assert (question, answer) == ("provenance 问", "legacy body markdown")


def test_annotation_section_exports_excerpt_without_qa_block(assistant_fixture):
    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )
    annotation = _annotation_row({
        "id": "excerpt-1",
        "book_source_revision_id": "rev",
        "pdf_page_index": 3,
        "kind": "AI_NOTE",
        "quote": "总线事务",
        "body": "> 什么是总线事务？\n\n总线事务由地址、数据与控制信号组成。",
        "highlight_style": "NONE",
        "source_kind": "AI_SAVED",
        "verification_state": None,
        "provenance_json": '{"answer_question": "什么是总线事务？"}',
        "source_grounding_json": None,
        "payload_json": (
            '{"question": "什么是总线事务？", "answer": "总线事务由地址、数据与控制信号组成。",'
            ' "excerpt": "总线事务由地址、数据与控制信号组成。", "page_index": 3}'
        ),
        "created_at": "2026-10-03T09:00:00",
    })
    lines = exporter._annotation_section(annotation, 1)
    text = "\n".join(lines)
    assert "**摘录自 AI 回答**" in text
    assert "总线事务由地址、数据与控制信号组成。" in text
    assert "> 当时的问题：什么是总线事务？" in text
    # 摘录没有审查，也不该套用问答结构
    assert "**问**" not in text and "**答**" not in text
    assert "AI 审查" not in text


def test_filename_sanitization_dedupe_and_save_location(assistant_fixture):
    revision = assistant_fixture["revision_id"]
    exporter = make_exporter(
        assistant_fixture["database"], assistant_fixture["data_root"]
    )

    name = exporter.build_filename(revision, page_index=3, topic='总线/事务："定时"同*步')
    assert name.endswith(".md")
    assert "assistant" in name
    assert "第4页" in name
    assert "总线事务定时同步" in name
    for illegal in '<>:"/\\|?*':
        assert illegal not in name

    plain = exporter.build_filename(revision, ordinal=2, topic=None)
    assert "第2条" in plain

    assert _sanitize_filename('a<b>:"c|d?*e\f') == "abcde"
    assert _sanitize_filename("   ") == "export"

    path = exporter.save_to_data_dir(revision, "# 内容", name)
    assert path.exists()
    assert path.parent == ManagedPaths(assistant_fixture["data_root"]).resolve("exports")
    assert path.read_text(encoding="utf-8") == "# 内容"

    duplicate = exporter.save_to_data_dir(revision, "# 内容", name)
    assert duplicate != path
    assert duplicate.stem.startswith(path.stem)
    assert duplicate.stem.endswith("(2)")


def test_save_markdown_to_path(tmp_path):
    """另存为对话框路径直写：无扩展名补 .md，同名覆盖不加密序号。"""
    from reader_service.export_md import save_markdown_to_path

    target = tmp_path / "自选目录" / "我的笔记"
    result = save_markdown_to_path("# 内容", target)
    assert result == tmp_path / "自选目录" / "我的笔记.md"
    assert result.read_text(encoding="utf-8") == "# 内容"

    # 用户点名路径 → 同名直接覆盖（覆盖确认由对话框负责）
    save_markdown_to_path("# 新内容", target)
    assert result.read_text(encoding="utf-8") == "# 新内容"


def test_save_markdown_to_path(tmp_path):
    """另存为对话框路径直写：无扩展名补 .md，同名覆盖不加密序号。"""
    from reader_service.export_md import save_markdown_to_path

    target = tmp_path / "自选目录" / "我的笔记"
    result = save_markdown_to_path("# 内容", target)
    assert result == tmp_path / "自选目录" / "我的笔记.md"
    assert result.read_text(encoding="utf-8") == "# 内容"

    # 用户点名路径 → 同名直接覆盖（覆盖确认由对话框负责）
    save_markdown_to_path("# 新内容", target)
    assert result.read_text(encoding="utf-8") == "# 新内容"
