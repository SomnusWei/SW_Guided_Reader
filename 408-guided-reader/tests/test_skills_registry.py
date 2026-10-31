from __future__ import annotations

import logging
from pathlib import Path

import pytest

from reader_service.assistant.skill import (
    EXPLANATION_SKILL_PATH,
    build_system_prompt,
    load_explanation_skill,
    load_skill_body,
)
from reader_service.skills.registry import (
    BUILTIN_SKILL_NAME,
    DEFAULT_SKILL_SOURCE_ENV,
    SkillInstaller,
    SkillRecord,
    SkillRegistry,
    ensure_default_skills,
    parse_frontmatter,
)
from reader_service.skills import restore_skill

# dailyread-skill lives next to the 408-guided-reader checkout, not inside it.
DAILYREAD_SKILL_DIR = Path(__file__).resolve().parents[2] / "dailyread-skill" / "SKILL"


def write_skill(root: Path, name: str, document: str) -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(document, encoding="utf-8")
    return skill_dir


VALID_SKILL = """---
name: demo-skill
description: 一个用于测试的演示技能
trigger_words: 中医, 针灸
---

# 演示技能

这是正文内容。
"""


class TestFrontmatterParsing:
    def test_parse_valid_document(self):
        meta, body = parse_frontmatter(VALID_SKILL)
        assert meta["name"] == "demo-skill"
        assert meta["description"] == "一个用于测试的演示技能"
        assert body.startswith("# 演示技能")
        assert "frontmatter" not in body

    def test_parse_block_scalar_and_list(self):
        document = "---\nname: x\ndescription: |\n  第一行\n  第二行\ntrigger_words:\n  - 甲\n  - 乙\n---\n\nbody\n"
        meta, body = parse_frontmatter(document)
        assert meta["description"] == "第一行\n第二行"
        assert meta["trigger_words"] == ["甲", "乙"]
        assert body == "body"

    def test_missing_frontmatter_returns_none(self):
        assert parse_frontmatter("# 没有前置元数据\n") is None

    def test_unclosed_frontmatter_returns_none(self):
        assert parse_frontmatter("---\nname: x\n") is None


class TestRegistryScan:
    def test_scan_filters_invalid_skill_directories(self, tmp_path, caplog):
        write_skill(tmp_path, "good", VALID_SKILL)
        write_skill(tmp_path, "no-frontmatter", "# 没有前置元数据\n")
        write_skill(tmp_path, "unclosed", "---\nname: broken\n")
        broken = write_skill(tmp_path, "no-body", "---\nname: empty-body\n---\n\n   \n")
        (broken / "SKILL.md").unlink()
        with caplog.at_level(logging.WARNING):
            records = SkillRegistry(tmp_path).scan()
        names = {record.name for record in records}
        assert "good" in names
        assert "no-frontmatter" not in names
        assert "unclosed" not in names
        assert "no-body" not in names
        assert any("no-frontmatter" in message for message in caplog.messages)

    def test_scan_always_includes_builtin_skill(self, tmp_path):
        records = SkillRegistry(tmp_path).scan()
        builtin = [record for record in records if record.name == BUILTIN_SKILL_NAME]
        assert len(builtin) == 1
        assert builtin[0].is_default is True
        assert builtin[0].enabled is True
        assert builtin[0].body == load_explanation_skill()

    def test_get_returns_record_and_raises_for_missing(self, tmp_path):
        registry = SkillRegistry(tmp_path)
        assert registry.get(BUILTIN_SKILL_NAME).name == BUILTIN_SKILL_NAME
        with pytest.raises(LookupError):
            registry.get("missing-skill")

    def test_record_fields_from_directory(self, tmp_path):
        skill_dir = write_skill(tmp_path, "demo", VALID_SKILL)
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "guide.md").write_text("ref", encoding="utf-8")
        (skill_dir / "scripts").mkdir()
        record = SkillRegistry(tmp_path).get("demo")
        assert record.display_name == "demo-skill"
        assert record.description == "一个用于测试的演示技能"
        assert record.trigger_words == ["中医", "针灸"]
        assert record.references_dir == skill_dir / "references"
        assert record.scripts_dir == skill_dir / "scripts"
        assert record.is_default is False


class TestEnabledPersistence:
    def test_set_enabled_persists_across_instances(self, tmp_path):
        write_skill(tmp_path, "demo", VALID_SKILL)
        registry = SkillRegistry(tmp_path)
        registry.set_enabled("demo", False)
        assert SkillRegistry(tmp_path).get("demo").enabled is False
        SkillRegistry(tmp_path).set_enabled("demo", True)
        assert SkillRegistry(tmp_path).get("demo").enabled is True

    def test_builtin_cannot_be_disabled(self, tmp_path):
        registry = SkillRegistry(tmp_path)
        with pytest.raises(ValueError):
            registry.set_enabled(BUILTIN_SKILL_NAME, False)

    def test_corrupt_config_is_ignored(self, tmp_path):
        write_skill(tmp_path, "demo", VALID_SKILL)
        (tmp_path / "skills" / "config.json").write_text("{broken", encoding="utf-8")
        assert SkillRegistry(tmp_path).get("demo").enabled is True


class TestInstaller:
    def test_install_copies_text_layer_only(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        installed = tmp_path / "skills" / "demo"
        assert (installed / "SKILL.md").is_file()
        assert (installed / "references" / "guide.md").is_file()
        assert (installed / "references" / "sub" / "deep.md").is_file()
        assert not (installed / "scripts").exists()
        assert not (installed / "中医教材").exists()
        assert record.name == "demo"
        assert record.display_name == "demo-skill"
        assert record.references_dir == installed / "references"

    def test_install_uses_frontmatter_name_by_default(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir)
        assert record.name == "demo-skill"
        assert (tmp_path / "skills" / "demo-skill" / "SKILL.md").is_file()

    def test_install_rejects_invalid_sources(self, tmp_path):
        installer = SkillInstaller(tmp_path)
        with pytest.raises(ValueError):
            installer.install_from_directory(tmp_path / "missing")
        empty = write_skill(tmp_path.parent, "no-md", VALID_SKILL)
        with pytest.raises(ValueError):
            installer.install_from_directory(empty.parent)

    def test_install_rejects_invalid_frontmatter(self, tmp_path):
        bad = write_skill(tmp_path, "bad-source", "没有 frontmatter")
        with pytest.raises(ValueError):
            SkillInstaller(tmp_path / "data").install_from_directory(bad)

    def test_install_rejects_invalid_name(self, tmp_path, source_dir):
        with pytest.raises(ValueError):
            SkillInstaller(tmp_path).install_from_directory(source_dir, "../escape")

    def test_reinstall_same_name_overwrites(self, tmp_path, source_dir):
        installer = SkillInstaller(tmp_path)
        installer.install_from_directory(source_dir, "demo")
        stale = tmp_path / "skills" / "demo" / "references" / "stale.md"
        stale.write_text("old", encoding="utf-8")
        (source_dir / "SKILL.md").write_text(
            VALID_SKILL.replace("demo-skill", "demo-skill-v2"), encoding="utf-8"
        )
        installer.install_from_directory(source_dir, "demo")
        assert not stale.exists()
        assert SkillRegistry(tmp_path).get("demo").display_name == "demo-skill-v2"

    def test_uninstall_removes_skill_and_config_entry(self, tmp_path):
        write_skill(tmp_path, "demo", VALID_SKILL)
        registry = SkillRegistry(tmp_path)
        registry.set_enabled("demo", False)
        SkillInstaller(tmp_path).uninstall("demo")
        assert not (tmp_path / "skills" / "demo").exists()
        assert all(record.name != "demo" for record in registry.scan())

    def test_uninstall_protects_builtin(self, tmp_path):
        with pytest.raises(ValueError):
            SkillInstaller(tmp_path).uninstall(BUILTIN_SKILL_NAME)

    def test_uninstall_missing_skill_raises(self, tmp_path):
        with pytest.raises(LookupError):
            SkillInstaller(tmp_path).uninstall("missing")


class TestDefaultSkillBootstrap:
    """2.0：补种是「整目录」语义——幂等、不覆盖用户改动、缺件补齐。"""

    def test_seed_installs_once_and_is_idempotent(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        first = ensure_default_skills(tmp_path, source_dir)
        assert first.installed == ["demo-skill"]
        assert (tmp_path / "skills" / "demo-skill" / "SKILL.md").is_file()
        second = ensure_default_skills(tmp_path, source_dir)
        assert second.installed == []
        assert second.skipped == ["demo-skill"]

    def test_seed_uses_environment_variable(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.setenv(DEFAULT_SKILL_SOURCE_ENV, str(source_dir))
        result = ensure_default_skills(tmp_path)
        assert result.installed == ["demo-skill"]

    def test_seed_ignores_invalid_source(self, tmp_path, monkeypatch):
        monkeypatch.setenv(DEFAULT_SKILL_SOURCE_ENV, str(tmp_path / "nowhere"))
        assert ensure_default_skills(tmp_path).unavailable is True
        assert ensure_default_skills(tmp_path, None).unavailable is True

    def test_seed_does_not_overwrite_edited_skill_md(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        ensure_default_skills(tmp_path, source_dir)
        edited = tmp_path / "skills" / "demo-skill" / "SKILL.md"
        edited.write_text("用户改过的正文", encoding="utf-8")
        ensure_default_skills(tmp_path, source_dir)
        assert edited.read_text(encoding="utf-8") == "用户改过的正文"

    def test_seed_fills_missing_files(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        ensure_default_skills(tmp_path, source_dir)
        (tmp_path / "skills" / "demo-skill" / "references" / "guide.md").unlink()
        result = ensure_default_skills(tmp_path, source_dir)
        assert result.filled == ["demo-skill"]
        assert (tmp_path / "skills" / "demo-skill" / "references" / "guide.md").is_file()

    def test_seed_skips_textbook_directories(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        ensure_default_skills(tmp_path, source_dir)
        assert not (tmp_path / "skills" / "demo-skill" / "中医教材").exists()

    def test_seed_handles_multi_skill_root(self, tmp_path, monkeypatch):
        root = tmp_path / "default-skills"
        for name in ("demo-a", "demo-b"):
            skill = root / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: x\n---\n\n正文\n", encoding="utf-8"
            )
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        result = ensure_default_skills(tmp_path, root)
        assert sorted(result.installed) == ["demo-a", "demo-b"]

    def test_restore_overwrites_edits(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        ensure_default_skills(tmp_path, source_dir)
        edited = tmp_path / "skills" / "demo-skill" / "SKILL.md"
        edited.write_text("坏掉的内容", encoding="utf-8")
        record = restore_skill(tmp_path, "demo-skill", source_dir)
        assert record.name == "demo-skill"
        assert "演示技能" in edited.read_text(encoding="utf-8")

    def test_restore_unknown_skill_raises(self, tmp_path, source_dir, monkeypatch):
        monkeypatch.delenv(DEFAULT_SKILL_SOURCE_ENV, raising=False)
        with pytest.raises(LookupError):
            restore_skill(tmp_path, "nope", source_dir)


class TestSkillPromptAssembly:
    def test_load_skill_body_appends_reference_index(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        body = load_skill_body(record)
        assert body.startswith("# 演示技能")
        assert "可用参考文件" in body
        assert "- guide.md" in body

    def test_load_skill_body_without_references(self, tmp_path):
        record = SkillRecord(
            name="bare", display_name="bare", description="", body="正文"
        )
        assert load_skill_body(record) == "正文"

    def test_build_system_prompt_assembles_all_parts(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        prompt = build_system_prompt("基础边界", record, "本节摘录")
        assert prompt.index("基础边界") < prompt.index("# 演示技能")
        assert prompt.index("# 演示技能") < prompt.index("本节摘录")

    def test_build_system_prompt_without_skill(self):
        assert build_system_prompt("基础边界") == "基础边界"
        assert build_system_prompt("基础边界", None, "摘录") == "基础边界\n\n摘录"


class TestRealDailyreadSkill:
    @pytest.mark.skipif(
        not (DAILYREAD_SKILL_DIR / "SKILL.md").is_file(),
        reason="dailyread-skill source not available",
    )
    def test_parse_real_skill_package(self, tmp_path):
        record = SkillInstaller(tmp_path).install_from_directory(DAILYREAD_SKILL_DIR)
        assert record.name == "yixue-zonghe"
        assert record.body.strip()
        assert "医" in record.description or len(record.description) > 0
        assert record.references_dir is not None
        index = load_skill_body(record)
        assert "可用参考文件" in index
        assert any(entry.endswith(".md") for entry in index.splitlines())
        installed = tmp_path / "skills" / "yixue-zonghe"
        assert not (installed / "scripts").exists()
        assert not (installed / "中医教材").exists()
        assert not (installed / "仓颉").exists()

    @pytest.mark.skipif(
        not (DAILYREAD_SKILL_DIR / "SKILL.md").is_file(),
        reason="dailyread-skill source not available",
    )
    def test_registry_reads_real_skill_from_data_dir(self, tmp_path):
        SkillInstaller(tmp_path).install_from_directory(DAILYREAD_SKILL_DIR)
        records = SkillRegistry(tmp_path).scan()
        by_name = {record.name: record for record in records}
        assert BUILTIN_SKILL_NAME in by_name
        assert by_name["yixue-zonghe"].enabled is True
        assert by_name["yixue-zonghe"].is_default is False


@pytest.fixture
def source_dir(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
    (src / "references").mkdir()
    (src / "references" / "guide.md").write_text("参考内容", encoding="utf-8")
    (src / "references" / "sub").mkdir()
    (src / "references" / "sub" / "deep.md").write_text("深", encoding="utf-8")
    (src / "scripts").mkdir()
    (src / "scripts" / "run.py").write_text("print('x')", encoding="utf-8")
    (src / "中医教材").mkdir()
    (src / "中医教材" / "book.txt").write_text("教材", encoding="utf-8")
    return src


class TestExtraSectionsHook:
    """§13.13 #2：2.1 的检索块从第 4 参注入，且必须排在最末。"""

    def test_extra_sections_are_appended_last(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        prompt = build_system_prompt(
            "边界", record, "课文章节", ["【教材片段】\n降逆汤"]
        )
        assert prompt.index("边界") < prompt.index("演示技能")
        assert prompt.index("课文章节") < prompt.index("【教材片段】")
        assert prompt.rstrip().endswith("降逆汤")

    def test_extra_sections_default_none_keeps_old_output(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        without = build_system_prompt("边界", record, "课文章节")
        with_none = build_system_prompt("边界", record, "课文章节", None)
        with_empty = build_system_prompt("边界", record, "课文章节", [])
        assert without == with_none == with_empty

    def test_blank_extra_sections_are_ignored(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        prompt = build_system_prompt("边界", record, None, ["", "   ", None])
        assert prompt == build_system_prompt("边界", record, None)


class TestExtraSectionsHook:
    """§13.13 #2：2.1 的检索块从第 4 参注入，且必须排在最末。"""

    def test_extra_sections_are_appended_last(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        prompt = build_system_prompt(
            "边界", record, "课文章节", ["【教材片段】\n降逆汤"]
        )
        assert prompt.index("边界") < prompt.index("演示技能")
        assert prompt.index("课文章节") < prompt.index("【教材片段】")
        assert prompt.rstrip().endswith("降逆汤")

    def test_extra_sections_default_none_keeps_old_output(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        without = build_system_prompt("边界", record, "课文章节")
        with_none = build_system_prompt("边界", record, "课文章节", None)
        with_empty = build_system_prompt("边界", record, "课文章节", [])
        assert without == with_none == with_empty

    def test_blank_extra_sections_are_ignored(self, tmp_path, source_dir):
        record = SkillInstaller(tmp_path).install_from_directory(source_dir, "demo")
        prompt = build_system_prompt("边界", record, None, ["", "   ", None])
        assert prompt == build_system_prompt("边界", record, None)
