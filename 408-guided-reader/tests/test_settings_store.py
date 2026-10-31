"""settings.json 存储层与外观路由（M5）。

settings.json 要在第一屏就被读到，所以它独立于 sqlite；这里的测试盯三件事：
主题只认三档、库根目录校验挡住系统目录、写盘是原子的（半截 JSON 读回默认值）。
"""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from reader_service.settings_store import (
    FILE_NAME,
    SettingsError,
    ReaderSettings,
    validate_library_root,
)


@pytest.fixture()
def settings(tmp_path: Path) -> ReaderSettings:
    return ReaderSettings(tmp_path)


def test_theme_defaults_to_system_without_file(settings: ReaderSettings, tmp_path: Path):
    assert settings.theme() == "system"
    assert not (tmp_path / FILE_NAME).exists(), "读默认值不该把文件写出来"


def test_theme_only_accepts_three_values(settings: ReaderSettings):
    for value in ("light", "dark", "system"):
        assert settings.set_theme(value) == value
        assert settings.theme() == value
    for bad in ("sepia", "DARK", "", None):
        with pytest.raises(SettingsError):
            settings.set_theme(bad)  # type: ignore[arg-type]


def test_theme_survives_reopen_and_keeps_other_keys(settings: ReaderSettings):
    settings.set_theme("dark")
    # 模拟另一个功能往同一份文件里写了别的段
    data = settings.load()
    data["custom"] = {"keep": True}
    settings._save(data)

    reopened = ReaderSettings(settings.data_dir)
    assert reopened.theme() == "dark"
    assert reopened.load()["custom"] == {"keep": True}


def test_broken_settings_file_falls_back_to_defaults(settings: ReaderSettings, tmp_path: Path):
    (tmp_path / FILE_NAME).write_text('{"appearance": {"theme": "da', encoding="utf-8")
    assert settings.theme() == "system"
    assert settings.onboarding_done() is False


def test_atomic_write_leaves_no_tmp(settings: ReaderSettings, tmp_path: Path):
    settings.set_theme("light")
    assert not list(tmp_path.glob("*.tmp")), "原子替换后不能留临时文件"
    payload = json.loads((tmp_path / FILE_NAME).read_text(encoding="utf-8"))
    assert payload["version"] == 1


def test_onboarding_flag_roundtrip(settings: ReaderSettings):
    assert settings.onboarding_done() is False
    settings.set_onboarding_done(True)
    assert settings.onboarding_done() is True


def test_library_root_defaults_to_blobs(settings: ReaderSettings):
    assert settings.library_root() == settings.data_dir / "blobs"
    stats = settings.library_stats()
    assert stats["is_default"] is True


def test_library_root_validation_blocks_root_and_system(tmp_path: Path):
    assert validate_library_root(str(tmp_path / "书库")) == (tmp_path / "书库").resolve()
    for bad in ("", "  ", "相对/路径"):
        with pytest.raises(SettingsError):
            validate_library_root(bad)
    with pytest.raises(SettingsError):
        validate_library_root("c:\\windows\\Temp")
    with pytest.raises(SettingsError):
        validate_library_root("c:\\")
    # 指向一个文件而不是目录
    a_file = tmp_path / "book.pdf"
    a_file.write_bytes(b"%PDF-1.4")
    with pytest.raises(SettingsError):
        validate_library_root(str(a_file))


def test_library_root_persists(settings: ReaderSettings, tmp_path: Path):
    target = tmp_path / "外部书库"
    target.mkdir()
    resolved = settings.set_library_root(str(target))
    assert settings.library_root() == resolved
    assert settings.library_stats()["is_default"] is False


def test_appearance_routes_and_onboarding(service):
    """GET/PUT /api/settings/appearance 与 onboarding 标记走 HTTP 的行为。"""
    import json as jsonlib
    from urllib.error import HTTPError

    from test_api import request_json, running_server

    with running_server(service) as (base, token):
        status, payload = request_json(f"{base}/api/settings/appearance", token)
        assert status == 200
        assert payload == {"theme": "system", "themes": ["light", "dark", "system"]}

        status, payload = request_json(
            f"{base}/api/settings/appearance",
            token,
            method="PUT",
            data=jsonlib.dumps({"theme": "dark"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert (status, payload["theme"]) == (200, "dark")

        # 非法值 → 400，且没有把非法值写进文件
        try:
            request_json(
                f"{base}/api/settings/appearance",
                token,
                method="PUT",
                data=jsonlib.dumps({"theme": "sepia"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            raise AssertionError("sepia 应该被拒绝")
        except HTTPError as error:
            assert error.code == 400
        assert ReaderSettings(service.paths.root).theme() == "dark"

        status, payload = request_json(f"{base}/api/onboarding", token)
        assert (status, payload["done"]) == (200, False)

        status, _ = request_json(
            f"{base}/api/onboarding/complete", token, method="POST", data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        status, payload = request_json(f"{base}/api/onboarding", token)
        assert payload["done"] is True


def test_onboarding_done_for_users_with_books(service):
    """回归：1.x 升级用户已有书、settings 里没有 onboarding 字段，也不该弹引导。"""
    from io import BytesIO

    from conftest import make_pdf
    from test_api import request_json, running_server

    pdf = make_pdf()
    service.intake(BytesIO(pdf), content_length=len(pdf), filename="升级前导入.pdf")
    assert ReaderSettings(service.paths.root).onboarding_done() is False

    with running_server(service) as (base, token):
        status, payload = request_json(f"{base}/api/onboarding", token)
        assert (status, payload["done"]) == (200, True)
        assert payload["books"] == 1
