"""appbase：随包资源基目录定位（装机验收发现的 frozen 路径 bug 回归测试）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from reader_service.appbase import candidate_bases, find_bundled_dir

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dev_mode_bases_contain_repo_root() -> None:
    bases = candidate_bases()
    assert REPO_ROOT in bases


def test_dev_mode_finds_bundled_plugins() -> None:
    found = find_bundled_dir("plugins")
    assert found is not None
    assert (found / "dict").is_dir()


def test_dev_mode_finds_default_skills() -> None:
    found = find_bundled_dir("default-skills")
    assert found is not None
    assert (found / "yixue-zonghe" / "SKILL.md").is_file()


def test_frozen_mode_uses_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    meipass = tmp_path / "_internal"
    (meipass / "plugins").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    bases = candidate_bases()
    assert bases and bases[0] == meipass
    assert find_bundled_dir("plugins") == meipass / "plugins"


def test_frozen_mode_falls_back_to_exe_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe_dir = tmp_path / "reader-service"
    (exe_dir / "plugins").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "reader-service.exe"), raising=False)
    bases = candidate_bases()
    assert exe_dir in bases
    assert find_bundled_dir("plugins") == exe_dir / "plugins"


def test_missing_dir_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)
    assert find_bundled_dir("no-such-bundle") is None


def test_registry_bundled_root_respects_meipass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from reader_service.plugins.registry import PluginRegistry

    meipass = tmp_path / "_internal"
    (meipass / "plugins" / "dict").mkdir(parents=True)
    monkeypatch.delenv("GUIDED_READER_PLUGINS_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    registry = PluginRegistry(data_dir=tmp_path / "data")
    assert registry.bundled_root == meipass / "plugins"


def test_seed_root_respects_meipass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from reader_service.skills.seed import resolve_seed_root

    meipass = tmp_path / "_internal"
    (meipass / "default-skills" / "demo").mkdir(parents=True)
    monkeypatch.delenv("GUIDED_READER_DEFAULT_SKILLS_DIR", raising=False)
    monkeypatch.delenv("GUIDED_READER_SKILL_DIR", raising=False)
    monkeypatch.delenv("GUIDED_READER_DEFAULT_SKILL_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    assert resolve_seed_root() == meipass / "default-skills"
