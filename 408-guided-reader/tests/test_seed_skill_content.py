"""随包技能种子（default-skills）的契约与内容护栏。

§13.13 #4/#5 是硬要求：种子版 SKILL.md 必须
  * 说明运行环境（无脚本、无教材原文），否则模型会"自信地编检索结果"；
  * 把检索写成条件式（有片段则引用并标编号，无片段则直接作答）；
  * 正文 ≤8000 字，否则 2.1 的检索块会被裁掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reader_service.skills import seed_default_skills
from reader_service.skills.registry import SkillRegistry, parse_frontmatter
from reader_service.skills.seed import resolve_seed_root

SEED_ROOT = Path(__file__).resolve().parents[1] / "default-skills"


@pytest.fixture(scope="module")
def seed_skill_md() -> str:
    path = SEED_ROOT / "yixue-zonghe" / "SKILL.md"
    if not path.is_file():
        pytest.skip("仓库里没有 default-skills/yixue-zonghe（发布包才带）")
    return path.read_text(encoding="utf-8")


def test_seed_directory_is_discoverable():
    if not SEED_ROOT.is_dir():
        pytest.skip("仓库里没有 default-skills")
    assert resolve_seed_root(SEED_ROOT) == SEED_ROOT


def test_seed_skill_frontmatter_is_valid(seed_skill_md):
    parsed = parse_frontmatter(seed_skill_md)
    assert parsed is not None
    meta, body = parsed
    assert meta["name"] == "yixue-zonghe"
    assert body.strip()


def test_seed_skill_body_within_budget(seed_skill_md):
    _, body = parse_frontmatter(seed_skill_md)
    assert len(body) <= 8000, f"SKILL.md 正文 {len(body)} 字，超过 8000 字上限"


def test_seed_skill_states_reader_runtime(seed_skill_md):
    for needle in ("没有脚本执行能力", "没有教材全文", "不得声称已经检索"):
        assert needle in seed_skill_md, f"缺少运行环境声明：{needle}"


def test_seed_skill_retrieval_is_conditional(seed_skill_md):
    # §13.13 #4：条件式措辞，2.1 上线前不会诱发幻觉
    assert "【教材片段" in seed_skill_md
    assert "标注编号" in seed_skill_md or "标注对应编号" in seed_skill_md or "[n]" in seed_skill_md
    assert "若" in seed_skill_md


def test_seed_skill_lands_and_is_scannable(tmp_path):
    if not SEED_ROOT.is_dir():
        pytest.skip("仓库里没有 default-skills")
    result = seed_default_skills(tmp_path, SEED_ROOT)
    assert result.installed == ["yixue-zonghe"]
    record = next(
        item for item in SkillRegistry(tmp_path).scan() if item.name == "yixue-zonghe"
    )
    assert record.enabled is True
    assert record.references_dir is not None
    # 资料层不随包：种子里不该有教材目录
    target = tmp_path / "skills" / "yixue-zonghe"
    assert not (target / "中医教材").exists()
    assert not (target / "platform").exists()
