from __future__ import annotations

from pathlib import Path

from reader_service.skills.registry import REFERENCE_INDEX_HEADING, SkillRecord


EXPLANATION_SKILL_PATH = Path(__file__).with_name("skills").joinpath(
    "assistant-explanation", "SKILL.md"
)


def load_explanation_skill(path: Path = EXPLANATION_SKILL_PATH) -> str:
    """Load the compact runtime skill body, excluding discovery frontmatter."""

    document = path.read_text(encoding="utf-8")
    if not document.startswith("---\n"):
        raise RuntimeError("Assistant Explanation Skill frontmatter is missing")
    parts = document.split("---", 2)
    if len(parts) != 3 or not parts[2].strip():
        raise RuntimeError("Assistant Explanation Skill body is missing")
    return parts[2].strip()


def load_skill_body(record: SkillRecord) -> str:
    """Skill body for prompt injection, plus the references/ index when present."""

    sections = [record.body]
    if record.references_dir is not None:
        entries = sorted(
            path.relative_to(record.references_dir).as_posix()
            for path in record.references_dir.rglob("*")
            if path.is_file()
        )
        if entries:
            listing = "\n".join(f"- {entry}" for entry in entries)
            sections.append(
                f"## {REFERENCE_INDEX_HEADING}\n\n"
                "以下文件位于该技能的 references/ 目录，可作为补充资料按需引用：\n"
                f"{listing}"
            )
    return "\n\n".join(section for section in sections if section.strip())


def build_system_prompt(
    base_prompt: str,
    skill_record: SkillRecord | None = None,
    section_excerpt: str | None = None,
    extra_sections: list[str] | None = None,
) -> str:
    """Assemble the assistant system prompt: base boundary + skill body (+ excerpt).

    第 4 参 ``extra_sections`` 是 2.1「教材检索」留的钩子（方案 §13.13 #2）：
    检索到的教材片段必须排在**最末**——模型对末尾内容注意力最高，而且这样
    2.0 的既有三处调用一行都不用改（2.0 里恒传 None）。
    """

    parts = []
    if base_prompt and base_prompt.strip():
        parts.append(base_prompt.strip())
    if skill_record is not None:
        parts.append(load_skill_body(skill_record))
    if section_excerpt and section_excerpt.strip():
        parts.append(section_excerpt.strip())
    for section in extra_sections or ():
        if isinstance(section, str) and section.strip():
            parts.append(section.strip())
    return "\n\n".join(parts)
