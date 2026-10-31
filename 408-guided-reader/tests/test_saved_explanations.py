from __future__ import annotations

import json
import threading
from io import BytesIO
from urllib.error import HTTPError

import pytest

from reader_service.agent_runtime import ProviderFailure, ProviderFailureKind
from reader_service.annotation import AnnotationRepository, AnnotationService
from reader_service.assistant import AssistantService, AssistantStateError
from reader_service.assistant.service import AssistantNode
from reader_service.foundation import FoundationRepository, FoundationService
from reader_service.library import LibraryService
from reader_service.saved_explanations import SavedExplanationService
from reader_service.storage import ManagedPaths

from test_ask_about_this import (
    assistant_fixture,
    create_child_for_text,
    focused,
    runtime_set,
    selection,
    NeverEngine,
)
from test_api import request_json, running_server
from conftest import make_pdf


def ai_markdown(question, answer):
    return f"> {' '.join(question.split())}\n\n{answer}"


def services(assistant_fixture, *, assistant_answers, review_answers, keys=None):
    providers, adapters = runtime_set(
        bakeoff_enabled=False,
        keys=keys,
        outcomes={
            "deepseek": tuple(assistant_answers),
            "zhipu": tuple(review_answers),
        },
    )
    assistant = AssistantService(assistant_fixture["contexts"], providers)
    annotations = AnnotationService(
        assistant_fixture["foundation"],
        AnnotationRepository(assistant_fixture["database"]),
    )
    saved = SavedExplanationService(
        assistant, annotations, providers, review_provider="zhipu"
    )
    return assistant, annotations, saved, providers, adapters


def test_root_and_child_save_once_with_exact_source_provenance_and_content(
    assistant_fixture,
):
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("根回答包含像乐队里的节拍器", "子回答的**完整**正文"),
        review_answers=(
            '{"verdict":"PASS","summary":"根回答在给定依据内可接受。"}',
            '{"verdict":"PASS","summary":"子回答在给定依据内可接受。"}',
        ),
    )
    session_id = "reader-session-save-root-child"
    root_state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    root = focused(root_state)
    root_projection = assistant.completed_turn_projection(
        session_id, root["root_id"], root["turns"][0]["turn_id"]
    )
    root_save = saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=root["root_id"],
        node_id=None,
        turn_id=root["turns"][0]["turn_id"],
        save_intent_id="save-intent-root-answer",
    )
    assert root_save["created"] is True
    assert root_save["annotation"]["verification_state"] == "PENDING"

    child_state = create_child_for_text(
        assistant, session_id, root_state, "像乐队里的节拍器"
    )
    child = focused(child_state)
    child_projection = assistant.completed_turn_projection(
        session_id,
        child["root_id"],
        child["turns"][0]["turn_id"],
        node_id=child["node_id"],
    )
    child_save = saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=child["root_id"],
        node_id=child["node_id"],
        turn_id=child["turns"][0]["turn_id"],
        save_intent_id="save-intent-child-answer",
    )
    assert child_save["created"] is True
    assert child_save["annotation"]["verification_state"] == "PENDING"
    assert saved.wait_for_idle()

    rows = annotations.list_page(assistant_fixture["revision_id"], 3)
    assert len(rows) == 2
    by_intent = {row["save_intent_id"]: row for row in rows}
    root_row = by_intent["save-intent-root-answer"]
    child_row = by_intent["save-intent-child-answer"]
    for row, projection in ((root_row, root_projection), (child_row, child_projection)):
        assert row["source_kind"] == "AI_SAVED"
        assert row["pdf_page_index"] == projection["pdf_page_index"] == 3
        assert row["quads"] == projection["source"]["quads"]
        assert row["quote"] == projection["source"]["quote"] == "总线事务"
        assert row["context_before"] == projection["source"]["context_before"]
        assert row["context_after"] == projection["source"]["context_after"]
        assert row["foundation_version_at_creation"] == 1
        assert row["body"] == ai_markdown(
            projection["provenance"]["answer_question"], projection["ai_content"]
        )
        assert row["payload"] == {
            "question": projection["provenance"]["answer_question"],
            "answer": projection["ai_content"],
            "quote": projection["source"]["quote"],
            "page_index": projection["pdf_page_index"],
        }
        assert row["verification_state"] == "PASS"
        assert row["review_provider"] == "zhipu"
        assert row["review_model"] == "GLM-5.3-Flash"
    assert root_row["provenance"] == {
        "root_focus": "总线事务",
        "child_focus": None,
        "concept_path": ["总线事务"],
        "answer_question": "总线事务",
    }
    assert child_row["provenance"] == {
        "root_focus": "总线事务",
        "child_focus": "像乐队里的节拍器",
        "concept_path": ["总线事务", "像乐队里的节拍器"],
        "answer_question": "像乐队里的节拍器",
    }


def test_excerpt_save_keeps_only_selected_text_and_skips_review(assistant_fixture):
    answer = "根回答包含像乐队里的节拍器，用于说明节拍关系。"
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=(answer,),
        review_answers=(),
    )
    session_id = "reader-session-save-excerpt"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    turn_id = current["turns"][0]["turn_id"]
    start = answer.index("节拍器")
    end = start + len("节拍器")

    result = saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=current["root_id"],
        node_id=None,
        turn_id=turn_id,
        save_intent_id="save-intent-excerpt-1",
        excerpt={"text": "节拍器", "start": start, "end": end},
    )
    row = result["annotation"]
    assert result["created"] is True
    # 摘录是原文片段，不进 AI 审查队列，也不该留下 PENDING
    assert result["review_scheduled"] is False
    assert row["verification_state"] is None
    assert row["kind"] == "AI_NOTE"
    assert row["source_kind"] == "AI_SAVED"
    # 只存选中的那段，不存整段回答
    assert row["payload"]["excerpt"] == "节拍器"
    assert row["payload"]["answer"] == "节拍器"
    assert row["payload"]["question"] == "总线事务"
    assert "节拍器" in row["body"]
    assert answer not in row["body"]
    # 仍沿用该轮的原文锚点与页码，才能写回 PDF 并可跳页
    assert row["pdf_page_index"] == 3
    assert row["quote"]
    assert row["quads"]
    assert row["provenance"]["answer_question"] == "总线事务"

    replay = saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=current["root_id"],
        node_id=None,
        turn_id=turn_id,
        save_intent_id="save-intent-excerpt-1",
        excerpt={"text": "节拍器", "start": start, "end": end},
    )
    assert replay["created"] is False
    assert replay["annotation"]["id"] == row["id"]
    assert replay["review_scheduled"] is False


def test_excerpt_save_rejects_text_that_does_not_match_the_answer(assistant_fixture):
    answer = "根回答包含像乐队里的节拍器。"
    assistant, _annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=(answer,),
        review_answers=(),
    )
    session_id = "reader-session-save-excerpt-invalid"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    turn_id = current["turns"][0]["turn_id"]
    common = {
        "reader_session_id": session_id,
        "root_id": current["root_id"],
        "node_id": None,
        "turn_id": turn_id,
    }
    with pytest.raises(ValueError, match="不一致"):
        saved.save(
            assistant_fixture["revision_id"],
            **common,
            save_intent_id="save-intent-excerpt-bad-text",
            excerpt={"text": "并不是原文", "start": 0, "end": 5},
        )
    with pytest.raises(ValueError, match="超出"):
        saved.save(
            assistant_fixture["revision_id"],
            **common,
            save_intent_id="save-intent-excerpt-bad-range",
            excerpt={"text": "节拍器", "start": 0, "end": len(answer) + 40},
        )
    with pytest.raises(ValueError, match="所选内容为空"):
        saved.save(
            assistant_fixture["revision_id"],
            **common,
            save_intent_id="save-intent-excerpt-empty",
            excerpt={"text": "   ", "start": 0, "end": 3},
        )


def test_excerpt_save_across_inline_markers_resolves_by_spans(assistant_fixture):
    """跨粗体等行内标记的选区：外沿区间会把 ** 切进来导致不一致，按段切片才能通过。"""
    answer = "依据是**「本虚标实」**四个字。"
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=(answer,),
        review_answers=(),
    )
    session_id = "reader-session-save-excerpt-spans"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    turn_id = current["turns"][0]["turn_id"]
    common = {
        "reader_session_id": session_id,
        "root_id": current["root_id"],
        "node_id": None,
        "turn_id": turn_id,
    }
    visible = "依据是「本虚标实」四个字。"
    # 外沿 start/end（复现用户报障的旧路径）：切片含 ** 标记，必须拒绝
    with pytest.raises(ValueError, match="不一致"):
        saved.save(
            assistant_fixture["revision_id"],
            **common,
            save_intent_id="save-intent-excerpt-spans-outer",
            excerpt={"text": visible, "start": 0, "end": len(answer)},
        )
    # 按可见文本段给出多段区间：成功，且存的是不含标记的可见文字
    spans = [
        {"start": 0, "end": 3},    # 依据是
        {"start": 5, "end": 11},   # 「本虚标实」（跳过 **）
        {"start": 13, "end": 17},  # 四个字。（跳过 **）
    ]
    result = saved.save(
        assistant_fixture["revision_id"],
        **common,
        save_intent_id="save-intent-excerpt-spans-ok",
        excerpt={"text": visible, "start": 0, "end": len(answer), "spans": spans},
    )
    assert result["created"] is True
    assert result["annotation"]["payload"]["excerpt"] == visible
    # 非法 spans：乱序重叠 / 越界 / 形状错误
    for bad in (
        [{"start": 5, "end": 11}, {"start": 3, "end": 4}],
        [{"start": 0, "end": len(answer) + 5}],
        [{"start": 0}],
        "not-a-list",
    ):
        with pytest.raises(ValueError):
            saved.save(
                assistant_fixture["revision_id"],
                **common,
                save_intent_id=f"save-intent-excerpt-spans-bad-{abs(hash(str(bad)))}",
                excerpt={"text": visible, "start": 0, "end": len(answer), "spans": bad},
            )


def test_excerpt_save_prefers_front_end_markdown(assistant_fixture):
    """前端反向序列化的 markdown 版本优先入库；可见文本一致性校验仍然生效。"""
    answer = "依据是**「本虚标实」**四个字。"
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=(answer,),
        review_answers=(),
    )
    session_id = "reader-session-save-excerpt-md"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    turn_id = current["turns"][0]["turn_id"]
    common = {
        "reader_session_id": session_id,
        "root_id": current["root_id"],
        "node_id": None,
        "turn_id": turn_id,
    }
    visible = "依据是「本虚标实」四个字。"
    spans = [
        {"start": 0, "end": 3},
        {"start": 5, "end": 11},
        {"start": 13, "end": 17},
    ]
    # 带 markdown：存的是带格式的版本，而不是纯文本切片
    result = saved.save(
        assistant_fixture["revision_id"],
        **common,
        save_intent_id="save-intent-excerpt-md-ok",
        excerpt={
            "text": visible, "start": 0, "end": len(answer), "spans": spans,
            "markdown": "依据是**「本虚标实」**四个字。",
        },
    )
    assert result["annotation"]["payload"]["excerpt"] == "依据是**「本虚标实」**四个字。"
    # markdown 形状不对（非字符串/纯空白）：退回纯文本切片，保存不失败
    for bad_markdown in (123, "   "):
        fallback = saved.save(
            assistant_fixture["revision_id"],
            **common,
            save_intent_id=f"save-intent-excerpt-md-bad-{abs(hash(str(bad_markdown)))}",
            excerpt={
                "text": visible, "start": 0, "end": len(answer), "spans": spans,
                "markdown": bad_markdown,
            },
        )
        assert fallback["annotation"]["payload"]["excerpt"] == visible
    # 未知字段依旧拒绝
    with pytest.raises(ValueError, match="摘录参数不完整"):
        saved.save(
            assistant_fixture["revision_id"],
            **common,
            save_intent_id="save-intent-excerpt-md-extra",
            excerpt={
                "text": visible, "start": 0, "end": len(answer), "spans": spans,
                "markdown": visible, "extra": 1,
            },
        )


def test_excerpt_save_repairs_truncated_markdown_structure(assistant_fixture):
    """选区截断表格/围栏/强调时自动补齐，批注内保持正确 Markdown 格式。"""
    answer = (
        "结论如下：\n\n"
        "| 证型 | 主症 |\n| --- | --- |\n| 气虚 | 乏力 |\n| 血瘀 | 刺痛 |\n\n"
        "另外 `代码` 与 **加粗** 都完整。\n\n"
        "表格从中间开始选就会丢表头：\n| 血瘀 | 刺痛 |"
    )
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=(answer,),
        review_answers=(),
    )
    session_id = "reader-session-save-excerpt-repair"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    turn_id = current["turns"][0]["turn_id"]
    common = {
        "reader_session_id": session_id,
        "root_id": current["root_id"],
        "node_id": None,
        "turn_id": turn_id,
    }
    # 选区只框住最后一行表格（无表头/分隔行）
    start = answer.index("| 血瘀 | 刺痛 |")
    end = start + len("| 血瘀 | 刺痛 |")
    result = saved.save(
        assistant_fixture["revision_id"],
        **common,
        save_intent_id="save-intent-excerpt-repair-table",
        excerpt={"text": "| 血瘀 | 刺痛 |", "start": start, "end": end},
    )
    excerpt = result["annotation"]["payload"]["excerpt"]
    lines = excerpt.split("\n")
    assert len(lines) == 3, excerpt
    assert lines[0].count("|") == 3  # 补出的空表头
    assert "---" in lines[1]         # 补出的分隔行
    assert lines[2] == "| 血瘀 | 刺痛 |"

    # 选区切在加粗词内部：存入内容补齐 ** 保持成对
    answer2 = "这里有一个 **未闭合的加粗片段"
    assistant2, annotations2, saved2, _p2, _a2 = services(
        assistant_fixture,
        assistant_answers=(answer2,),
        review_answers=(),
    )
    state2 = assistant2.ask_selection(
        session_id + "-2", assistant_fixture["revision_id"], 3, **selection(3)
    )
    current2 = focused(state2)
    result2 = saved2.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id + "-2",
        root_id=current2["root_id"],
        node_id=None,
        turn_id=current2["turns"][0]["turn_id"],
        save_intent_id="save-intent-excerpt-repair-bold",
        excerpt={
            "text": "这里有一个 **未闭合的加粗片段",
            "start": 0,
            "end": len(answer2),
        },
    )
    excerpt2 = result2["annotation"]["payload"]["excerpt"]
    assert excerpt2.count("**") % 2 == 0
    assert excerpt2.endswith("**")


def test_excerpt_save_http_passes_selection_and_skips_review(assistant_fixture):
    answer = "根回答包含像乐队里的节拍器。"
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=(answer,),
        review_answers=(),
    )
    session_id = "reader-session-save-excerpt-http"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    library = LibraryService(ManagedPaths(assistant_fixture["data_root"]))
    start = answer.index("节拍器")
    common = {
        "reader_session_id": session_id,
        "root_id": current["root_id"],
        "node_id": None,
        "turn_id": current["turns"][0]["turn_id"],
        "save_intent_id": "http-excerpt-intent",
    }
    with running_server(
        library,
        annotations=annotations,
        assistant=assistant,
        saved_explanations=saved,
    ) as (base, token):
        url = f"{base}/api/revisions/{assistant_fixture['revision_id']}/assistant/save"
        status, response = request_json(
            url,
            token,
            method="POST",
            data=json.dumps({
                **common,
                "excerpt": {"text": "节拍器", "start": start, "end": start + 3},
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert status == 201
        assert response["annotation"]["payload"]["excerpt"] == "节拍器"
        assert response["annotation"]["verification_state"] is None
        assert response["review_scheduled"] is False

        with pytest.raises(HTTPError) as failure:
            request_json(
                url,
                token,
                method="POST",
                data=json.dumps({
                    **common,
                    "save_intent_id": "http-excerpt-bad-shape",
                    "excerpt": {"text": "节拍器", "start": start},
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
        assert failure.value.code == 400
        assert json.load(failure.value)["code"] == "INVALID_SAVE_EXPLANATION"


def test_save_intent_is_durably_idempotent_under_concurrent_replay(assistant_fixture):
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("一次且仅一次的回答",),
        review_answers=(),
        keys={"deepseek": "deepseek-secret"},
    )
    session_id = "reader-session-save-idempotency"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    barrier = threading.Barrier(4)
    results = []

    def save_once():
        barrier.wait()
        results.append(saved.save(
            assistant_fixture["revision_id"],
            reader_session_id=session_id,
            root_id=current["root_id"],
            node_id=None,
            turn_id=current["turns"][0]["turn_id"],
            save_intent_id="same-logical-save-intent",
        ))

    threads = [threading.Thread(target=save_once) for _ in range(3)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert len(results) == 3
    assert sum(result["created"] for result in results) == 1
    assert len({result["annotation"]["id"] for result in results}) == 1
    assert len(annotations.list_page(assistant_fixture["revision_id"], 3)) == 1
    assert saved.wait_for_idle()
    row = annotations.list_page(assistant_fixture["revision_id"], 3)[0]
    assert row["verification_state"] == "TECHNICAL_FAILURE"
    assert row["review_failure_kind"] == "UNCONFIGURED"
    assert row["review_provider"] is None
    assert row["review_model"] is None


def test_durable_commit_and_save_result_do_not_wait_for_review(assistant_fixture):
    assistant, annotations, saved, _providers, adapters = services(
        assistant_fixture,
        assistant_answers=("先保存再审查",),
        review_answers=(),
    )
    entered = threading.Event()
    release = threading.Event()

    def blocking_review(_endpoint, _api_key, _body, _timeout):
        entered.set()
        assert release.wait(timeout=3)
        return '{"verdict":"PASS","summary":"审查在保存之后完成。"}'

    adapters["zhipu"].complete = blocking_review
    session_id = "reader-session-save-before-review"
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
        save_intent_id="save-before-blocking-review",
    )
    assert entered.wait(timeout=1)
    assert not release.is_set()
    assert result["annotation"]["verification_state"] == "PENDING"
    committed = annotations.get(
        assistant_fixture["revision_id"], result["annotation"]["id"]
    )
    assert committed["body"] == ai_markdown("总线事务", "先保存再审查")
    assert committed["verification_state"] == "PENDING"
    release.set()
    assert saved.wait_for_idle()
    reviewed = annotations.get(assistant_fixture["revision_id"], committed["id"])
    assert reviewed["verification_state"] == "PASS"


def test_review_allowlist_semantic_fail_and_zero_learning_writes(assistant_fixture):
    assistant, annotations, saved, providers, adapters = services(
        assistant_fixture,
        assistant_answers=("父回答包含子焦点", "需要被拒绝的子回答"),
        review_answers=(
            '{"verdict":"FAIL","summary":"解释超出了当前教材依据。"}',
        ),
    )
    annotations.create_text(
        assistant_fixture["revision_id"],
        page_index=3,
        start=selection(3)["start"],
        end=selection(3)["end"],
        body="OTHER_NOTE_SECRET_CANARY",
    )
    session_id = "reader-session-review-allowlist"
    root_state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    child_state = create_child_for_text(assistant, session_id, root_state, "子焦点")
    child = focused(child_state)
    slot = assistant._sessions[session_id]
    with slot.lock:
        root = slot.state.roots[child["root_id"]]
        root.reader_session_id = "TREE_SESSION_CANARY"
        root.reference_context["navigation"] = "NAVIGATION_CANARY"
        root.nodes["unrelated-sibling"] = AssistantNode(
            node_id="unrelated-sibling",
            parent_node_id=None,
            depth=2,
            selected_text="SIBLING_FOCUS_CANARY",
        )
    original_body = child["turns"][0]["answer"]
    saved_result = saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=child["root_id"],
        node_id=child["node_id"],
        turn_id=child["turns"][0]["turn_id"],
        save_intent_id="review-allowlist-save",
    )
    assert saved_result["annotation"]["body"] == ai_markdown("子焦点", original_body)
    assert saved.wait_for_idle()
    row = annotations.get(
        assistant_fixture["revision_id"], saved_result["annotation"]["id"]
    )
    assert row["verification_state"] == "FAIL"
    assert row["body"] == ai_markdown("子焦点", original_body)
    assert row["quote"] == "总线事务"
    assert row["provenance"]["child_focus"] == "子焦点"

    review_body = adapters["zhipu"].calls[0]["body"]
    assert [message["role"] for message in review_body["messages"]] == ["system", "user"]
    transported = json.loads(review_body["messages"][1]["content"])
    assert set(transported) == {"source", "provenance", "ai_content"}
    assert set(transported["source"]) == {
        "pdf_page_number", "printed_page_label", "chapter_title", "section_title",
        "quote", "context_before", "context_after", "bounded_same_page_ocr",
    }
    assert set(transported["provenance"]) == {
        "root_focus", "child_focus", "concept_path", "answer_question",
    }
    transport_text = json.dumps(review_body, ensure_ascii=False)
    for canary in (
        "OTHER_NOTE_SECRET_CANARY",
        "TREE_SESSION_CANARY",
        "NAVIGATION_CANARY",
        "SIBLING_FOCUS_CANARY",
        "deepseek-secret",
        "zhipu-secret",
        "save_intent_id",
        "active_child_id",
        "knowledge_point_id",
    ):
        assert canary not in transport_text
    inspected = json.dumps(providers.inspector.snapshot(), ensure_ascii=False)
    assert "zhipu-secret" not in inspected
    assert "Authorization" not in inspected

    with assistant_fixture["database"].connect() as connection:
        tables = {
            item[0] for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table in ("master_threads", "master_topics", "master_messages", "learning_events", "kp_status", "teaching_assets"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    forbidden = {
        "mastery", "progress", "learning_history", "knowledge",
        "teaching",
    }
    assert tables.isdisjoint(forbidden)


def test_invalid_review_verdict_is_technical_and_retry_updates_same_annotation(
    assistant_fixture,
):
    assistant, annotations, saved, _providers, adapters = services(
        assistant_fixture,
        assistant_answers=("保持不变的正文",),
        review_answers=("not-json", '{"verdict":"MAYBE","summary":"无效"}'),
    )
    session_id = "reader-session-review-retry"
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
        save_intent_id="invalid-verdict-save",
    )
    assert saved.wait_for_idle()
    failed = annotations.get(assistant_fixture["revision_id"], result["annotation"]["id"])
    assert failed["verification_state"] == "TECHNICAL_FAILURE"
    assert failed["review_failure_kind"] == "INVALID_STRUCTURED_VERDICT"
    assert failed["body"] == ai_markdown("总线事务", "保持不变的正文")
    assert len(adapters["zhipu"].calls) == 2

    adapters["zhipu"].outcomes.append(
        '{"verdict":"PASS","summary":"重试后结构与语义均可接受。"}'
    )
    retry = saved.retry_review(assistant_fixture["revision_id"], failed["id"])
    assert retry["review_scheduled"] is True
    assert saved.wait_for_idle()
    passed = annotations.get(assistant_fixture["revision_id"], failed["id"])
    assert passed["id"] == failed["id"]
    assert passed["verification_state"] == "PASS"
    assert passed["body"] == failed["body"]
    assert len(annotations.list_page(assistant_fixture["revision_id"], 3)) == 1


def test_interrupted_pending_review_survives_restart_without_assistant_tree(
    assistant_fixture,
):
    assistant, annotations, _saved, providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("重启后仍可恢复的正文",),
        review_answers=(
            '{"verdict":"PASS","summary":"从 durable allowlist 重试后通过。"}',
        ),
    )
    session_id = "reader-session-interrupted-review"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    projection = assistant.completed_turn_projection(
        session_id, current["root_id"], current["turns"][0]["turn_id"]
    )
    pending, created = annotations.create_ai_saved(
        assistant_fixture["revision_id"],
        save_intent_id="interrupted-review-save",
        projection=projection,
    )
    assert created is True
    assistant.close_session(session_id)
    assert assistant.conversation_count() == 0

    restarted_library = LibraryService(ManagedPaths(assistant_fixture["data_root"]))
    restarted_foundation = FoundationService(
        restarted_library,
        FoundationRepository(restarted_library.database),
        NeverEngine,
    )
    restarted_annotations = AnnotationService(
        restarted_foundation,
        AnnotationRepository(restarted_library.database),
    )
    recovered = restarted_annotations.get(
        assistant_fixture["revision_id"], pending["id"]
    )
    assert recovered["verification_state"] == "PENDING"
    assert recovered["pdf_page_index"] == 3
    assert recovered["body"] == ai_markdown("总线事务", "重启后仍可恢复的正文")
    restarted_saved = SavedExplanationService(
        assistant, restarted_annotations, providers, review_provider="zhipu"
    )
    interrupted = restarted_annotations.get(
        assistant_fixture["revision_id"], pending["id"]
    )
    assert interrupted["verification_state"] == "TECHNICAL_FAILURE"
    assert interrupted["review_failure_kind"] == "INTERRUPTED"
    assert interrupted["review_code"] == "review_interrupted"
    assert interrupted["body"] == ai_markdown("总线事务", "重启后仍可恢复的正文")
    assert restarted_saved.retry_review(
        assistant_fixture["revision_id"], recovered["id"]
    )["review_scheduled"] is True
    assert restarted_saved.wait_for_idle()
    passed = restarted_annotations.get(
        assistant_fixture["revision_id"], recovered["id"]
    )
    assert passed["verification_state"] == "PASS"
    restarted_annotations.delete(assistant_fixture["revision_id"], passed["id"])

    reopened_library = LibraryService(ManagedPaths(assistant_fixture["data_root"]))
    reopened_foundation = FoundationService(
        reopened_library,
        FoundationRepository(reopened_library.database),
        NeverEngine,
    )
    reopened_annotations = AnnotationService(
        reopened_foundation,
        AnnotationRepository(reopened_library.database),
    )
    assert reopened_annotations.list_page(assistant_fixture["revision_id"], 3) == []


def test_book_delete_cascades_user_and_ai_saved_without_cross_book_effect(
    assistant_fixture,
):
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("随本书删除的 AI 正文",),
        review_answers=(
            '{"verdict":"PASS","summary":"删除前审查通过。"}',
        ),
    )
    annotations.create_text(
        assistant_fixture["revision_id"],
        page_index=3,
        start=selection(3)["start"],
        end=selection(3)["end"],
        body="随本书删除的 USER 笔记",
    )
    session_id = "reader-session-book-cascade"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    saved.save(
        assistant_fixture["revision_id"],
        reader_session_id=session_id,
        root_id=current["root_id"],
        node_id=None,
        turn_id=current["turns"][0]["turn_id"],
        save_intent_id="cascade-ai-save",
    )
    assert saved.wait_for_idle()
    library = LibraryService(ManagedPaths(assistant_fixture["data_root"]))
    first_book = next(
        book for book in library.list_books()
        if book["active_revision"]["id"] == assistant_fixture["revision_id"]
    )
    other_pdf = make_pdf()
    other_book = library.intake(
        BytesIO(other_pdf), content_length=len(other_pdf), filename="other-book.pdf"
    )["book"]
    library.delete_book(first_book["id"])
    remaining = library.list_books()
    assert [book["id"] for book in remaining] == [other_book["id"]]
    with library.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM annotations WHERE book_source_revision_id = ?",
            (assistant_fixture["revision_id"],),
        ).fetchone()[0] == 0


def test_pending_or_oversized_answer_cannot_be_claimed_as_saved(assistant_fixture):
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("完成的回答",),
        review_answers=(),
    )
    session_id = "reader-session-ineligible-save"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    slot = assistant._sessions[session_id]
    with slot.lock:
        root = slot.state.roots[current["root_id"]]
        root.pending_turn_id = "pending-turn-identity"
    with pytest.raises(AssistantStateError) as pending:
        saved.save(
            assistant_fixture["revision_id"],
            reader_session_id=session_id,
            root_id=current["root_id"],
            node_id=None,
            turn_id="pending-turn-identity",
            save_intent_id="pending-answer-save",
        )
    assert pending.value.code == "ANSWER_NOT_COMPLETED"

    with slot.lock:
        root.turns[0].answer = "x" * (annotations.MAX_AI_CONTENT_LENGTH + 1)
    with pytest.raises(ValueError, match="too large"):
        saved.save(
            assistant_fixture["revision_id"],
            reader_session_id=session_id,
            root_id=current["root_id"],
            node_id=None,
            turn_id=current["turns"][0]["turn_id"],
            save_intent_id="oversized-answer-save",
        )
    assert annotations.list_page(assistant_fixture["revision_id"], 3) == []


@pytest.mark.parametrize(
    ("failure", "expected_kind"),
    (
        (
            ProviderFailure(
                ProviderFailureKind.TRANSIENT, "network", "审查网络超时，可重试。"
            ),
            "TRANSIENT",
        ),
        (
            ProviderFailure(
                ProviderFailureKind.USER_ACTIONABLE, "auth", "审查凭据不可用，可重试。"
            ),
            "USER_ACTIONABLE",
        ),
    ),
)
def test_reviewer_provider_failure_preserves_saved_asset(
    assistant_fixture, failure, expected_kind
):
    assistant, annotations, saved, _providers, _adapters = services(
        assistant_fixture,
        assistant_answers=("审查失败也保留",),
        review_answers=(failure,),
    )
    session_id = f"reader-session-review-{expected_kind.lower()}"
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
        save_intent_id=f"review-failure-{expected_kind.lower()}",
    )
    assert result["annotation"]["verification_state"] == "PENDING"
    assert saved.wait_for_idle()
    row = annotations.get(assistant_fixture["revision_id"], result["annotation"]["id"])
    assert row["verification_state"] == "TECHNICAL_FAILURE"
    assert row["review_failure_kind"] == expected_kind
    assert row["body"] == ai_markdown("总线事务", "审查失败也保留")
    assert row["source_kind"] == "AI_SAVED"


def test_reviewer_cooling_is_retryable_and_never_claims_a_transport_call(
    assistant_fixture,
):
    assistant, annotations, saved, providers, adapters = services(
        assistant_fixture,
        assistant_answers=("冷却时仍先保存",),
        review_answers=(),
    )
    review_runtime = providers.runtimes["zhipu"]
    review_runtime._cooling_until = review_runtime.clock() + 30
    session_id = "reader-session-review-cooling"
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
        save_intent_id="cooling-review-save",
    )
    assert saved.wait_for_idle()
    row = annotations.get(assistant_fixture["revision_id"], result["annotation"]["id"])
    assert row["verification_state"] == "TECHNICAL_FAILURE"
    assert row["review_failure_kind"] == "COOLING"
    assert row["review_provider"] is None
    assert row["review_model"] is None
    assert adapters["zhipu"].calls == []


def test_save_and_review_retry_http_contract_is_commit_first(assistant_fixture):
    assistant, annotations, saved, _providers, adapters = services(
        assistant_fixture,
        assistant_answers=("HTTP 保存的完整正文",),
        review_answers=("invalid", "still invalid"),
    )
    session_id = "reader-session-save-http"
    state = assistant.ask_selection(
        session_id, assistant_fixture["revision_id"], 3, **selection(3)
    )
    current = focused(state)
    library = LibraryService(ManagedPaths(assistant_fixture["data_root"]))
    request_body = json.dumps({
        "reader_session_id": session_id,
        "root_id": current["root_id"],
        "node_id": None,
        "turn_id": current["turns"][0]["turn_id"],
        "save_intent_id": "http-save-intent",
    }).encode()
    with running_server(
        library,
        annotations=annotations,
        assistant=assistant,
        saved_explanations=saved,
    ) as (base, token):
        status, response = request_json(
            f"{base}/api/revisions/{assistant_fixture['revision_id']}/assistant/save",
            token,
            method="POST",
            data=request_body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 201
        annotation_id = response["annotation"]["id"]
        assert response["annotation"]["verification_state"] == "PENDING"
        assert response["created"] is True
        assert saved.wait_for_idle()
        failed = annotations.get(assistant_fixture["revision_id"], annotation_id)
        assert failed["verification_state"] == "TECHNICAL_FAILURE"

        status, replay = request_json(
            f"{base}/api/revisions/{assistant_fixture['revision_id']}/assistant/save",
            token,
            method="POST",
            data=request_body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert replay["created"] is False
        assert replay["annotation"]["id"] == annotation_id

        adapters["zhipu"].outcomes.append(
            '{"verdict":"PASS","summary":"HTTP 技术重试通过。"}'
        )
        status, retry = request_json(
            f"{base}/api/revisions/{assistant_fixture['revision_id']}/annotations/{annotation_id}/review",
            token,
            method="POST",
            data=b"",
        )
        assert status == 202
        assert retry["annotation"]["id"] == annotation_id
        assert saved.wait_for_idle()
        passed = annotations.get(assistant_fixture["revision_id"], annotation_id)
        assert passed["verification_state"] == "PASS"
        assert passed["body"] == ai_markdown("总线事务", "HTTP 保存的完整正文")
        assert len(annotations.list_page(assistant_fixture["revision_id"], 3)) == 1
