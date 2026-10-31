"""Skill registry over ``<data-dir>/skills/<skill-name>/SKILL.md`` packages.

Phase 1 covers the text layer only: frontmatter metadata, SKILL.md body and a
``references/`` index. ``scripts_dir`` is carried on the record for Phase 2 but
scripts are never executed or installed here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

BUILTIN_SKILL_NAME = "assistant-explanation"
DEFAULT_SKILL_SOURCE_ENV = "GUIDED_READER_DEFAULT_SKILL_DIR"
DESCRIPTION_MAX_CHARS = 200
REFERENCE_INDEX_HEADING = "可用参考文件"

_FIELD_PATTERN = re.compile(r"([A-Za-z_][\w-]*):\s*(.*)$")


@dataclass(slots=True)
class SkillRecord:
    """One installed skill: metadata plus the SKILL.md body (frontmatter stripped)."""

    name: str
    display_name: str
    description: str
    body: str
    trigger_words: list[str] = field(default_factory=list)
    references_dir: Path | None = None
    # Phase 2 placeholder: script execution is not implemented in Phase 1.
    scripts_dir: Path | None = None
    enabled: bool = True
    is_default: bool = False


def parse_frontmatter(text: str) -> tuple[dict, str] | None:
    """Parse a ``---`` delimited YAML-lite frontmatter block.

    Supports plain ``key: value`` scalars (with ``true``/``false`` coercion),
    ``|`` block scalars and ``- item`` lists. Returns ``(fields, body)`` or
    ``None`` when the document does not carry a well-formed frontmatter block.
    """

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return _parse_fields(lines[1:index]), "\n".join(lines[index + 1:]).strip()
    return None


def _parse_fields(lines: list[str]) -> dict:
    fields: dict = {}
    current_key: str | None = None
    block_mode = False
    block_lines: list[str] = []
    for raw in lines:
        if not raw.strip():
            if block_mode:
                block_lines.append("")
            continue
        if block_mode and raw[0] in " \t":
            block_lines.append(raw.strip())
            continue
        if block_mode:
            fields[current_key] = "\n".join(block_lines).strip()
            block_mode = False
            block_lines = []
        match = _FIELD_PATTERN.match(raw)
        if match and not raw[0].isspace():
            key, value = match.group(1), match.group(2).strip()
            if value == "|":
                current_key, block_mode, block_lines = key, True, []
            elif value == "":
                current_key = key
                fields.setdefault(key, [])
            else:
                fields[key] = _coerce_scalar(value)
                current_key = key
        elif raw.lstrip().startswith("- ") and current_key is not None:
            items = fields.get(current_key)
            if not isinstance(items, list):
                items = []
            items.append(raw.lstrip()[2:].strip().strip("\"'"))
            fields[current_key] = items
    if block_mode and current_key is not None:
        fields[current_key] = "\n".join(block_lines).strip()
    return fields


def _coerce_scalar(value: str):
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _trigger_words(meta: dict) -> list[str]:
    raw = meta.get("trigger_words") or meta.get("triggers")
    if raw is None:
        return []
    if isinstance(raw, list):
        pieces = [str(item) for item in raw]
    else:
        pieces = re.split(r"[,，、;；]", str(raw))
    return [piece.strip() for piece in pieces if piece.strip()]


def _clean_description(meta: dict) -> str:
    raw = meta.get("description") or ""
    if isinstance(raw, list):
        raw = "\n".join(str(item) for item in raw)
    clean = " ".join(str(raw).split())
    if len(clean) > DESCRIPTION_MAX_CHARS:
        return clean[: DESCRIPTION_MAX_CHARS - 1] + "…"
    return clean


class SkillRegistry:
    """Scans installed skills plus the built-in assistant-explanation skill."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.skills_dir = self.data_dir / "skills"
        self.config_path = self.skills_dir / "config.json"

    def scan(self) -> list[SkillRecord]:
        records: dict[str, SkillRecord] = {}
        builtin = self._builtin_record()
        if builtin is not None:
            records[builtin.name] = builtin
        enabled = self._load_enabled_config()
        if self.skills_dir.is_dir():
            for entry in sorted(self.skills_dir.iterdir()):
                if not entry.is_dir():
                    continue
                record = self._record_from_directory(entry)
                if record is None:
                    continue
                record.enabled = enabled.get(record.name, True)
                records[record.name] = record
        return list(records.values())

    def get(self, name: str) -> SkillRecord:
        for record in self.scan():
            if record.name == name:
                return record
        raise LookupError(f"技能不存在：{name}")

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name == BUILTIN_SKILL_NAME:
            raise ValueError("内置技能不可禁用")
        self.get(name)
        config = self._load_enabled_config()
        config[name] = bool(enabled)
        self._save_enabled_config(config)

    def _record_from_directory(self, entry: Path) -> SkillRecord | None:
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            logger.warning("技能目录缺少 SKILL.md，已跳过：%s", entry)
            return None
        parsed = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if parsed is None:
            logger.warning("SKILL.md frontmatter 无效，已跳过：%s", entry)
            return None
        meta, body = parsed
        if not body:
            logger.warning("SKILL.md 正文为空，已跳过：%s", entry)
            return None
        references_dir = entry / "references"
        scripts_dir = entry / "scripts"
        return SkillRecord(
            name=entry.name,
            display_name=str(meta.get("name") or entry.name),
            description=_clean_description(meta),
            body=body,
            trigger_words=_trigger_words(meta),
            references_dir=references_dir if references_dir.is_dir() else None,
            scripts_dir=scripts_dir if scripts_dir.is_dir() else None,
        )

    def _builtin_record(self) -> SkillRecord | None:
        # Lazy import keeps reader_service.skills importable without assistant.
        from reader_service.assistant.skill import EXPLANATION_SKILL_PATH

        if not EXPLANATION_SKILL_PATH.is_file():
            logger.warning("内置技能缺失：%s", EXPLANATION_SKILL_PATH)
            return None
        parsed = parse_frontmatter(EXPLANATION_SKILL_PATH.read_text(encoding="utf-8"))
        if parsed is None:
            logger.warning("内置技能 frontmatter 无效：%s", EXPLANATION_SKILL_PATH)
            return None
        meta, body = parsed
        if not body:
            return None
        return SkillRecord(
            name=BUILTIN_SKILL_NAME,
            display_name=str(meta.get("name") or BUILTIN_SKILL_NAME),
            description=_clean_description(meta),
            body=body,
            trigger_words=_trigger_words(meta),
            references_dir=None,
            enabled=True,
            is_default=True,
        )

    def _load_enabled_config(self) -> dict[str, bool]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        enabled = payload.get("enabled") if isinstance(payload, dict) else None
        if not isinstance(enabled, dict):
            return {}
        return {str(k): bool(v) for k, v in enabled.items()}

    def _save_enabled_config(self, enabled: dict[str, bool]) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps({"enabled": enabled}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class SkillInstaller:
    """Installs the text layer (SKILL.md + references/) of a skill package."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.skills_dir = self.data_dir / "skills"

    def install_from_directory(self, src_dir: Path, skill_name: str | None = None) -> SkillRecord:
        src = Path(src_dir)
        skill_md = src / "SKILL.md"
        if not skill_md.is_file():
            raise ValueError(f"源目录缺少 SKILL.md，安装中止：{src}")
        parsed = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if parsed is None:
            raise ValueError(f"SKILL.md frontmatter 无效，安装中止：{src}")
        meta, body = parsed
        if not body:
            raise ValueError(f"SKILL.md 正文为空，安装中止：{src}")
        name = skill_name or str(meta.get("name") or "") or src.name
        if not name or name in {".", ".."} or re.search(r"[\\/:*?\"<>|]", name):
            raise ValueError(f"技能名无效：{name!r}")
        target = self.skills_dir / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copy2(skill_md, target / "SKILL.md")
        references_src = src / "references"
        references_dir = None
        if references_src.is_dir():
            shutil.copytree(references_src, target / "references")
            references_dir = target / "references"
        scripts_dir = target / "scripts"
        return SkillRecord(
            name=name,
            display_name=str(meta.get("name") or name),
            description=_clean_description(meta),
            body=body,
            trigger_words=_trigger_words(meta),
            references_dir=references_dir,
            scripts_dir=scripts_dir if scripts_dir.is_dir() else None,
        )

    def uninstall(self, name: str) -> None:
        if name == BUILTIN_SKILL_NAME:
            raise ValueError("内置技能不可卸载")
        target = self.skills_dir / name
        if not target.is_dir():
            raise LookupError(f"技能不存在：{name}")
        shutil.rmtree(target)
        config_path = self.skills_dir / "config.json"
        registry = SkillRegistry(self.data_dir)
        enabled = registry._load_enabled_config()
        if name in enabled:
            del enabled[name]
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps({"enabled": enabled}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def ensure_default_skills(
    data_dir: Path, default_skill_source: str | os.PathLike | None = None
):
    """2.0：等价于 ``skills.seed.seed_default_skills``（整目录补种）。

    保留这个入口是为了兼容 1.x 的调用点与外部脚本；返回值已从
    ``SkillRecord | None`` 变为 ``SeedResult``——2.0 起补种是"整个
    default-skills 目录"，不再是"装一个技能"。
    """

    from reader_service.skills.seed import seed_default_skills

    return seed_default_skills(data_dir, default_skill_source)
