from __future__ import annotations

import copy
import json

from reader_service.foundation import FoundationService
from reader_service.foundation.geometry import parse_normalized_rect

from .repository import AnnotationRepository

_UNSET = object()

ANCHORED_KINDS = frozenset({"HIGHLIGHT", "UNDERLINE"})
RECT_KINDS = frozenset({"TEXTBOX", "REGION", "NOTE"})
KINDS = ANCHORED_KINDS | RECT_KINDS | frozenset({"AI_NOTE"})


class AnnotationService:
    MAX_NOTE_LENGTH = 1000
    MAX_AI_CONTENT_LENGTH = 100_000
    HIGHLIGHT_STYLES = frozenset({"YELLOW", "GREEN", "BLUE", "RED", "PURPLE", "NONE"})
    VERIFICATION_STATES = frozenset({"PENDING", "PASS", "FAIL", "TECHNICAL_FAILURE"})

    def __init__(self, foundation: FoundationService, repository: AnnotationRepository):
        self.foundation = foundation
        self.repository = repository

    def create(
        self,
        revision_id: str,
        *,
        kind: str,
        page_index: int,
        start: dict | None = None,
        end: dict | None = None,
        rect: list | None = None,
        body: str | None = None,
        highlight_style: str = "YELLOW",
    ) -> dict:
        if kind in ANCHORED_KINDS:
            if start is None or end is None:
                raise ValueError("Text-anchored annotations need a selection")
            return self.create_text(
                revision_id,
                page_index=page_index,
                start=start,
                end=end,
                body=body,
                highlight_style=highlight_style,
                kind=kind,
            )
        if kind in RECT_KINDS:
            return self._create_rect(
                revision_id,
                kind=kind,
                page_index=page_index,
                rect=rect,
                body=body,
                highlight_style=highlight_style,
            )
        raise ValueError("Annotation kind is invalid")

    def create_text(
        self,
        revision_id: str,
        *,
        page_index: int,
        start: dict,
        end: dict,
        body: str | None = None,
        highlight_style: str = "YELLOW",
        kind: str = "HIGHLIGHT",
    ) -> dict:
        if kind not in ANCHORED_KINDS:
            raise ValueError("Text-anchored annotations only support HIGHLIGHT or UNDERLINE")
        clean_body = self._clean_body(body)
        clean_highlight_style = self._clean_highlight_style(highlight_style)
        anchor = self.foundation.resolve_text_selection(
            revision_id,
            page_index,
            start=start,
            end=end,
        )
        return self.repository.create(
            revision_id=revision_id,
            page_index=page_index,
            kind=kind,
            quads=anchor["quads"],
            quote=anchor["quote"],
            context_before=anchor["context_before"],
            context_after=anchor["context_after"],
            foundation_version=anchor["foundation_version"],
            body=clean_body,
            highlight_style=clean_highlight_style,
        )

    def _create_rect(
        self,
        revision_id: str,
        *,
        kind: str,
        page_index: int,
        rect,
        body: str | None,
        highlight_style: str,
    ) -> dict:
        revision = self.foundation.ensure_revision(revision_id)
        if page_index < 0 or page_index >= revision["page_count"]:
            raise ValueError("Page index is outside this PDF")
        clean_rect = parse_normalized_rect(rect)
        clean_body = self._clean_body(body)
        clean_highlight_style = self._clean_highlight_style(highlight_style)
        if clean_highlight_style == "NONE":
            raise ValueError("Box annotations need a visible color")
        return self.repository.create(
            revision_id=revision_id,
            page_index=page_index,
            kind=kind,
            rect=clean_rect,
            body=clean_body,
            highlight_style=clean_highlight_style,
        )

    def list_page(self, revision_id: str, page_index: int) -> list[dict]:
        revision = self.foundation.ensure_revision(revision_id)
        if page_index < 0 or page_index >= revision["page_count"]:
            raise ValueError("Page index is outside this PDF")
        return self.repository.list_page(revision_id, page_index)

    def list_all(self, revision_id: str) -> list[dict]:
        self.foundation.ensure_revision(revision_id)
        return self.repository.list_all(revision_id)

    def get(self, revision_id: str, annotation_id: str) -> dict:
        self.foundation.ensure_revision(revision_id)
        return self.repository.get(annotation_id, revision_id)

    def find_by_save_intent(
        self, revision_id: str, save_intent_id: str
    ) -> dict | None:
        self.foundation.ensure_revision(revision_id)
        return self.repository.find_by_save_intent(
            self._clean_save_intent_id(save_intent_id), revision_id
        )

    def update(
        self,
        revision_id: str,
        annotation_id: str,
        *,
        body=_UNSET,
        color=_UNSET,
        rect=_UNSET,
    ) -> dict:
        self.foundation.ensure_revision(revision_id)
        annotation = self.repository.get(annotation_id, revision_id)
        if annotation["kind"] == "AI_NOTE":
            raise ValueError("Saved AI explanations cannot be edited here")
        fields: dict = {}
        if body is not _UNSET:
            fields["body"] = self._clean_body(body)
        if color is not _UNSET:
            cleaned_color = self._clean_highlight_style(color)
            if annotation["kind"] in RECT_KINDS and cleaned_color == "NONE":
                raise ValueError("Box annotations need a visible color")
            fields["highlight_style"] = cleaned_color
        if rect is not _UNSET:
            if annotation["kind"] not in RECT_KINDS:
                raise ValueError(
                    "Only textbox and region annotations can move; text anchors re-anchor themselves"
                )
            fields["rect_json"] = json.dumps(parse_normalized_rect(rect), separators=(",", ":"))
        if not fields:
            return annotation
        updated = self.repository.update_fields(annotation_id, revision_id, fields)
        if updated is None:
            raise LookupError("Annotation not found")
        return updated

    def create_ai_saved(
        self,
        revision_id: str,
        *,
        save_intent_id: str,
        projection: dict,
        excerpt: str | None = None,
    ) -> tuple[dict, bool]:
        revision = self.foundation.ensure_revision(revision_id)
        if projection.get("revision_id") != revision_id:
            raise ValueError("Saved explanation source revision does not match the request")
        page_index = projection.get("pdf_page_index")
        if not isinstance(page_index, int) or isinstance(page_index, bool):
            raise ValueError("Saved explanation PDF page is invalid")
        if page_index < 0 or page_index >= revision["page_count"]:
            raise ValueError("Saved explanation PDF page is outside this source revision")
        if excerpt is not None:
            if not isinstance(excerpt, str) or not excerpt.strip():
                raise ValueError("Excerpt content is empty")
            if len(excerpt) > self.MAX_AI_CONTENT_LENGTH:
                raise ValueError("Selected excerpt is too large to save")
        ai_content = projection.get("ai_content")
        if not isinstance(ai_content, str) or not ai_content.strip():
            raise ValueError("Only a completed Assistant answer can be saved")
        if len(ai_content) > self.MAX_AI_CONTENT_LENGTH:
            raise ValueError(
                "Assistant answer is too large to save completely; nothing was saved"
            )
        source = projection.get("source")
        provenance = projection.get("provenance")
        source_grounding = projection.get("source_grounding")
        if not isinstance(source, dict):
            raise ValueError("Saved explanation SOURCE is invalid")
        self._validate_provenance(provenance)
        self._validate_source_grounding(source_grounding)
        required_source = {
            "quads", "quote", "context_before", "context_after", "foundation_version"
        }
        if set(source) != required_source or not isinstance(source["quote"], str) \
                or not source["quote"]:
            raise ValueError("Saved explanation SOURCE is incomplete")
        question = provenance["answer_question"]
        if excerpt is None:
            payload = {
                "question": question,
                "answer": ai_content,
                "quote": source["quote"],
                "page_index": page_index,
            }
            body = self._render_ai_markdown(question, ai_content)
            verification_state = "PENDING"
        else:
            # 只存选中的原文片段：payload.excerpt 是权威内容，answer 与之保持一致，
            # 方便 PDF 写回、面板与导出统一读取；不进入 AI 审查队列。
            payload = {
                "question": question,
                "answer": excerpt,
                "excerpt": excerpt,
                "quote": source["quote"],
                "page_index": page_index,
            }
            body = self._render_ai_markdown(question, excerpt)
            verification_state = None
        return self.repository.create_ai_saved(
            revision_id=revision_id,
            page_index=page_index,
            quads=copy.deepcopy(source["quads"]),
            quote=source["quote"],
            context_before=source["context_before"],
            context_after=source["context_after"],
            foundation_version=source["foundation_version"],
            body=body,
            payload=payload,
            save_intent_id=self._clean_save_intent_id(save_intent_id),
            provenance=copy.deepcopy(provenance),
            source_grounding=copy.deepcopy(source_grounding),
            verification_state=verification_state,
        )

    @staticmethod
    def _render_ai_markdown(question: str, answer: str) -> str:
        collapsed = " ".join(question.split())
        return f"> {collapsed}\n\n{answer}"

    def update_verification(
        self,
        revision_id: str,
        annotation_id: str,
        *,
        state: str,
        provider: str | None = None,
        model: str | None = None,
        failure_kind: str | None = None,
        code: str | None = None,
        summary: str | None = None,
    ) -> dict | None:
        if state not in self.VERIFICATION_STATES - {"PENDING"}:
            raise ValueError("Verification result state is invalid")
        if summary is not None:
            if not isinstance(summary, str):
                raise ValueError("Verification summary must be text")
            summary = summary.strip()
            if len(summary) > 1000:
                raise ValueError("Verification summary must be at most 1000 characters")
        return self.repository.update_verification(
            annotation_id,
            revision_id,
            state=state,
            provider=provider,
            model=model,
            failure_kind=failure_kind,
            code=code,
            summary=summary or None,
        )

    def mark_review_pending(self, revision_id: str, annotation_id: str) -> dict:
        self.foundation.ensure_revision(revision_id)
        return self.repository.mark_review_pending(annotation_id, revision_id)

    def recover_interrupted_reviews(self) -> int:
        return self.repository.recover_interrupted_reviews()

    def delete(self, revision_id: str, annotation_id: str) -> None:
        self.foundation.ensure_revision(revision_id)
        if not self.repository.delete(annotation_id, revision_id):
            raise LookupError("Annotation not found")

    @staticmethod
    def _clean_save_intent_id(value: str) -> str:
        if (
            not isinstance(value, str)
            or not 8 <= len(value) <= 128
            or any(character.isspace() for character in value)
        ):
            raise ValueError("Save intent id is invalid")
        return value

    @staticmethod
    def _validate_provenance(value: object) -> None:
        expected = {"root_focus", "child_focus", "concept_path", "answer_question"}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Saved explanation PROVENANCE is invalid")
        if not isinstance(value["root_focus"], str) or not value["root_focus"].strip():
            raise ValueError("Saved explanation Root provenance is missing")
        if value["child_focus"] is not None and (
            not isinstance(value["child_focus"], str) or not value["child_focus"].strip()
        ):
            raise ValueError("Saved explanation Child provenance is invalid")
        if (
            not isinstance(value["answer_question"], str)
            or not value["answer_question"].strip()
            or not isinstance(value["concept_path"], list)
            or not value["concept_path"]
            or any(not isinstance(item, str) or not item.strip() for item in value["concept_path"])
        ):
            raise ValueError("Saved explanation concept path is invalid")

    @staticmethod
    def _validate_source_grounding(value: object) -> None:
        expected = {
            "pdf_page_number", "printed_page_label", "chapter_title",
            "section_title", "bounded_same_page_ocr",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Saved explanation Reader grounding is invalid")
        if not isinstance(value["pdf_page_number"], int) or value["pdf_page_number"] < 1:
            raise ValueError("Saved explanation Reader page is invalid")
        if not isinstance(value["bounded_same_page_ocr"], str):
            raise ValueError("Saved explanation Reader context is invalid")

    @classmethod
    def _clean_body(cls, body: str | None) -> str | None:
        if body is None:
            return None
        if not isinstance(body, str):
            raise ValueError("Note body must be text")
        value = body.strip()
        if not value:
            return None
        if len(value) > cls.MAX_NOTE_LENGTH:
            raise ValueError(f"Note body must be at most {cls.MAX_NOTE_LENGTH} characters")
        return value

    @classmethod
    def _clean_highlight_style(cls, highlight_style: str) -> str:
        if not isinstance(highlight_style, str):
            raise ValueError("Highlight style must be text")
        value = highlight_style.strip().upper()
        if value in cls.HIGHLIGHT_STYLES:
            return value
        # 自定义颜色：接受 #RRGGBB / RRGGBB，统一存成 #RRGGBB
        raw = value.lstrip("#")
        if len(raw) == 6 and all(char in "0123456789ABCDEF" for char in raw):
            return "#" + raw
        allowed = ", ".join(sorted(cls.HIGHLIGHT_STYLES)) + ", #RRGGBB"
        raise ValueError(f"Highlight style must be one of: {allowed}")
