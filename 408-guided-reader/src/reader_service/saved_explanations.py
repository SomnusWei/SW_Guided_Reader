from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass

from reader_service.agent_runtime import (
    ProviderCompletion,
    ProviderFailure,
    ProviderFailureKind,
    ProviderRuntimeSet,
)
from reader_service.annotation import AnnotationService
from reader_service.assistant import AssistantService
from reader_service.markdown_repair import repair_markdown_fragment


REVIEW_SYSTEM_MESSAGE = """你是独立的 AI 审查者，只判断一条已保存解释是否被给定教材依据支持。
Original PDF 教材来源始终是阅读权威；AI 解释可以补充通识或专业知识，但不得冒充教材原文。
PASS 仅表示本次有界 AI 审查未发现阻断问题，不是真理证明、教材权威或学习掌握证据。
不得改写正文，不得推断或写入 Mastery、Progress、Learning History、Master、Knowledge 或 Teaching。
只返回一个 JSON 对象，且只能含两个字段：
{"verdict":"PASS 或 FAIL","summary":"不超过 1000 字的简体中文理由"}
不要返回 Markdown 代码围栏或其他文字。"""

MAX_STRUCTURED_ATTEMPTS = 2


class SavedExplanationError(Exception):
    def __init__(self, code: str, user_message: str):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    verdict: str
    summary: str


class SavedExplanationService:
    """Commit-first bridge from temporary Assistant state to durable Annotation."""

    def __init__(
        self,
        assistant: AssistantService,
        annotations: AnnotationService,
        runtime: ProviderRuntimeSet,
        *,
        review_provider: str | None = None,
    ):
        self.assistant = assistant
        self.annotations = annotations
        self.runtime = runtime
        self.review_provider = (
            review_provider
            or os.environ.get("GUIDED_READER_REVIEW_PROVIDER", "zhipu")
        ).strip().lower()
        self._inflight: set[tuple[str, str]] = set()
        self._condition = threading.Condition()
        self.annotations.recover_interrupted_reviews()

    def save(
        self,
        revision_id: str,
        *,
        reader_session_id: str,
        root_id: str,
        node_id: str | None,
        turn_id: str,
        save_intent_id: str,
        excerpt: dict | None = None,
    ) -> dict:
        clean_excerpt = self._clean_excerpt_request(excerpt)
        existing = self.annotations.find_by_save_intent(revision_id, save_intent_id)
        if existing is not None:
            scheduled = (
                self._schedule_review(revision_id, existing["id"])
                if clean_excerpt is None and existing["verification_state"] == "PENDING"
                else False
            )
            return {"annotation": existing, "created": False, "review_scheduled": scheduled}

        projection = self.assistant.completed_turn_projection(
            reader_session_id,
            root_id,
            turn_id,
            node_id=node_id,
        )
        if clean_excerpt is not None:
            clean_excerpt = self._resolve_excerpt(projection["ai_content"], clean_excerpt)
        annotation, created = self.annotations.create_ai_saved(
            revision_id,
            save_intent_id=save_intent_id,
            projection=projection,
            excerpt=clean_excerpt,
        )
        # 摘录只是原文片段，不涉及"解释是否被教材支持"，因此不进 AI 审查队列
        scheduled = (
            False if clean_excerpt is not None else self._schedule_review(revision_id, annotation["id"])
        )
        return {
            "annotation": annotation,
            "created": created,
            "review_scheduled": scheduled,
        }

    @staticmethod
    def _clean_excerpt_request(excerpt: object) -> dict | None:
        """校验前端传来的选区范围；这里只做形状检查，正文稍后用回答原文切出。"""
        if excerpt is None:
            return None
        if not isinstance(excerpt, dict) or not {"text", "start", "end"} <= set(excerpt):
            raise ValueError("摘录参数不完整")
        extra = set(excerpt) - {"text", "start", "end", "spans", "markdown"}
        if extra:
            raise ValueError("摘录参数不完整")
        text = excerpt["text"]
        start = excerpt["start"]
        end = excerpt["end"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("所选内容为空，无法保存")
        for value in (start, end):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("摘录位置参数无效")
        if end <= start:
            raise ValueError("摘录位置参数无效")
        markdown = excerpt.get("markdown")
        if markdown is not None and (
            not isinstance(markdown, str) or not markdown.strip()
        ):
            # markdown 是可选增强字段，形状不对就退回纯文本切片，不让保存失败
            markdown = None
        spans = excerpt.get("spans")
        if spans is not None:
            # 跨行内标记（粗体/斜体等）的选区：前端按可见文本段给出多段原文区间，
            # 后端逐段切片拼接，避免把 ** 等标记切进摘录。
            if not isinstance(spans, list) or not spans:
                raise ValueError("摘录位置参数无效")
            cleaned_spans = []
            previous_end = -1
            for span in spans:
                if not isinstance(span, dict) or set(span) != {"start", "end"}:
                    raise ValueError("摘录位置参数无效")
                span_start, span_end = span["start"], span["end"]
                for value in (span_start, span_end):
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        raise ValueError("摘录位置参数无效")
                if span_end <= span_start or span_start < previous_end:
                    raise ValueError("摘录位置参数无效")
                previous_end = span_end
                cleaned_spans.append((span_start, span_end))
        else:
            cleaned_spans = None
        return {
            "text": text,
            "start": start,
            "end": end,
            "spans": cleaned_spans,
            "markdown": markdown.strip() if isinstance(markdown, str) else None,
        }

    @staticmethod
    def _resolve_excerpt(ai_content: str, excerpt: dict) -> str:
        """以回答原文为准切出摘录，避免前端偏移或文本被篡改。"""
        spans = excerpt["spans"]
        length = len(ai_content)
        if spans is not None:
            if spans[-1][1] > length:
                raise ValueError("摘录超出回答范围，请重新选择")
            sliced = "".join(ai_content[span_start:span_end] for span_start, span_end in spans)
        else:
            start, end = excerpt["start"], excerpt["end"]
            if end > length:
                raise ValueError("摘录超出回答范围，请重新选择")
            sliced = ai_content[start:end]
        if not sliced.strip():
            raise ValueError("所选内容为空，无法保存")
        normalize = lambda value: "".join(value.split())
        if normalize(sliced) != normalize(excerpt["text"]):
            raise ValueError("所选内容与回答原文不一致，请重新选择")
        # 前端把渲染后的选区反向序列化成了带格式的 markdown（源码切片只含可见
        # 文本，**/#/| 等结构性标记不在文本节点里，必然丢格式）。可见文本一致性
        # 已在上面验证过，这里优先存 markdown 版本；缺失时退回切片+结构修复。
        markdown = excerpt.get("markdown")
        if isinstance(markdown, str) and markdown.strip():
            return markdown.strip()
        # 选区可能把表格/代码围栏/成对强调标记切成一半；补齐结构语法，
        # 保证存进批注的内容按正确格式渲染而不是退化为纯文本。
        return repair_markdown_fragment(sliced.strip())

    def retry_review(self, revision_id: str, annotation_id: str) -> dict:
        annotation = self.annotations.get(revision_id, annotation_id)
        if annotation["source_kind"] != "AI_SAVED":
            raise SavedExplanationError(
                "REVIEW_NOT_APPLICABLE", "只有 AI 保存的解释需要这项审查。"
            )
        if annotation["verification_state"] not in {"PENDING", "TECHNICAL_FAILURE"}:
            raise SavedExplanationError(
                "REVIEW_NOT_RETRYABLE", "这条解释当前不需要技术重试。"
            )
        if annotation["verification_state"] == "TECHNICAL_FAILURE":
            annotation = self.annotations.mark_review_pending(
                revision_id, annotation_id
            )
        scheduled = self._schedule_review(revision_id, annotation_id)
        return {"annotation": annotation, "review_scheduled": scheduled}

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def _schedule_review(self, revision_id: str, annotation_id: str) -> bool:
        key = (revision_id, annotation_id)
        with self._condition:
            if key in self._inflight:
                return False
            self._inflight.add(key)
        thread = threading.Thread(
            target=self._run_review,
            args=(revision_id, annotation_id),
            name=f"saved-explanation-review-{annotation_id[:8]}",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()
            self.annotations.update_verification(
                revision_id,
                annotation_id,
                state="TECHNICAL_FAILURE",
                failure_kind=ProviderFailureKind.TRANSIENT.value,
                code="review_schedule_failure",
                summary="AI 审查未能启动；已保存内容不受影响，可重试。",
            )
            return False
        return True

    def _run_review(self, revision_id: str, annotation_id: str) -> None:
        key = (revision_id, annotation_id)
        try:
            try:
                annotation = self.annotations.get(revision_id, annotation_id)
            except LookupError:
                return
            if annotation["source_kind"] != "AI_SAVED" or annotation[
                "verification_state"
            ] not in {"PENDING", "TECHNICAL_FAILURE"}:
                return
            self._review_annotation(annotation)
        finally:
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()

    def _review_annotation(self, annotation: dict) -> None:
        interaction_id = f"review:{annotation['id']}"
        completion: ProviderCompletion | None = None
        for attempt in range(1, MAX_STRUCTURED_ATTEMPTS + 1):
            messages = self._review_messages(
                annotation,
                invalid_feedback=attempt > 1,
            )
            try:
                completion = self.runtime.complete_for_with_metadata(
                    self.review_provider,
                    messages,
                    interaction_id=interaction_id,
                )
            except ProviderFailure as failure:
                provider, model = self._attempted_identity(interaction_id)
                self.annotations.update_verification(
                    annotation["book_source_revision_id"],
                    annotation["id"],
                    state="TECHNICAL_FAILURE",
                    provider=provider,
                    model=model,
                    failure_kind=failure.kind.value,
                    code=failure.code,
                    summary=failure.user_message,
                )
                return
            except Exception:
                provider, model = self._attempted_identity(interaction_id)
                self.annotations.update_verification(
                    annotation["book_source_revision_id"],
                    annotation["id"],
                    state="TECHNICAL_FAILURE",
                    provider=provider,
                    model=model,
                    failure_kind=ProviderFailureKind.TRANSIENT.value,
                    code="review_runtime_failure",
                    summary="AI 审查出现内部技术失败；已保存内容不受影响，可重试。",
                )
                return
            try:
                verdict = self._validate_verdict(completion.answer)
            except ValueError:
                if attempt < MAX_STRUCTURED_ATTEMPTS:
                    continue
                provider, model = self._completion_identity(completion)
                self.annotations.update_verification(
                    annotation["book_source_revision_id"],
                    annotation["id"],
                    state="TECHNICAL_FAILURE",
                    provider=provider,
                    model=model,
                    failure_kind="INVALID_STRUCTURED_VERDICT",
                    code="invalid_review_verdict",
                    summary="AI 审查连续返回无效结构；已保存内容不受影响，可重试。",
                )
                return
            provider, model = self._completion_identity(completion)
            self.annotations.update_verification(
                annotation["book_source_revision_id"],
                annotation["id"],
                state=verdict.verdict,
                provider=provider,
                model=model,
                failure_kind=None,
                code=None,
                summary=verdict.summary,
            )
            return

    @staticmethod
    def _review_messages(annotation: dict, *, invalid_feedback: bool) -> list[dict]:
        provenance = annotation["provenance"]
        grounding = annotation["source_grounding"]
        payload = annotation.get("payload") or {}
        ai_content = payload.get("answer") or annotation["body"]
        candidate = {
            "source": {
                "pdf_page_number": grounding["pdf_page_number"],
                "printed_page_label": grounding["printed_page_label"],
                "chapter_title": grounding["chapter_title"],
                "section_title": grounding["section_title"],
                "quote": annotation["quote"],
                "context_before": annotation["context_before"],
                "context_after": annotation["context_after"],
                "bounded_same_page_ocr": grounding["bounded_same_page_ocr"],
            },
            "provenance": {
                "root_focus": provenance["root_focus"],
                "child_focus": provenance["child_focus"],
                "concept_path": list(provenance["concept_path"]),
                "answer_question": provenance["answer_question"],
            },
            "ai_content": ai_content,
        }
        system = REVIEW_SYSTEM_MESSAGE
        if invalid_feedback:
            system += "\n上一次输出未通过结构校验；请严格按同一 JSON schema 重试。"
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(candidate, ensure_ascii=False, separators=(",", ":")),
            },
        ]

    @staticmethod
    def _validate_verdict(answer: str) -> ReviewVerdict:
        try:
            value = json.loads(answer)
        except (TypeError, ValueError):
            raise ValueError("Review verdict is not JSON") from None
        if not isinstance(value, dict) or set(value) != {"verdict", "summary"}:
            raise ValueError("Review verdict fields are invalid")
        verdict = value["verdict"]
        summary = value["summary"]
        if verdict not in {"PASS", "FAIL"}:
            raise ValueError("Review verdict value is invalid")
        if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 1000:
            raise ValueError("Review summary is invalid")
        return ReviewVerdict(verdict, summary.strip())

    @staticmethod
    def _completion_identity(completion: ProviderCompletion) -> tuple[str | None, str | None]:
        config = completion.effective_config or {}
        return config.get("provider"), config.get("model")

    def _attempted_identity(self, interaction_id: str) -> tuple[str | None, str | None]:
        inspector = getattr(self.runtime, "inspector", None)
        if inspector is None:
            return None, None
        calls = inspector.snapshot()
        attempted = next(
            (item for item in reversed(calls) if item.get("interaction_id") == interaction_id),
            None,
        )
        if attempted is None:
            return None, None
        provider = attempted.get("provider")
        try:
            _, model = self.runtime.provider_identity(provider)
        except ProviderFailure:
            model = None
        return provider, model


__all__ = ["ReviewVerdict", "SavedExplanationError", "SavedExplanationService"]
