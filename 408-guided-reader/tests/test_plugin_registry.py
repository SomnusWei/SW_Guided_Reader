"""插件注册表：manifest 校验、双根优先级、启停持久化、静态资源穿越、zip 安装与卸载回收。"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from reader_service.plugins import (
    PluginError,
    PluginNotFound,
    PluginRegistry,
    clean_selection_text,
)

VALID_MANIFEST = {
    "schema": 1,
    "id": "dict",
    "name": "中医辞典",
    "version": "1.0.0",
    "description": "九部离线词典",
    "entry": "index.html",
    "icon": "icon.svg",
    "category": "查检",
    "surface": ["window", "selection"],
    "window": {"width": 980, "height": 720, "title": "中医辞典", "resizable": True},
    "selection": {
        "actions": [
            {
                "id": "lookup",
                "label": "词典",
                "title": "用中医辞典查所选文字",
                "order": 40,
                "maxChars": 60,
                "stripPunctuation": True,
            }
        ]
    },
    "permissions": ["read-own-assets"],
}


def write_plugin(root, plugin_id="dict", manifest=None, entry=True):
    directory = root / plugin_id
    (directory / "data").mkdir(parents=True, exist_ok=True)
    payload = dict(VALID_MANIFEST) if manifest is None else manifest
    payload.setdefault("id", plugin_id)
    (directory / "plugin.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    if entry:
        (directory / "index.html").write_text("<html>dict</html>", encoding="utf-8")
    (directory / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (directory / "data" / "ciku.json").write_text("{}", encoding="utf-8")
    return directory


@pytest.fixture
def registry(tmp_path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    bundled.mkdir()
    return PluginRegistry(tmp_path / "data", bundled_root=bundled, user_root=user)


# --------------------------------------------------------------- manifest 校验


def test_scan_reads_full_manifest(registry):
    write_plugin(registry.bundled_root)
    record = registry.get("dict")
    assert record.name == "中医辞典"
    assert record.version == "1.0.0"
    assert record.entry == "index.html"
    assert record.icon == "icon.svg"
    assert record.surfaces == ["window", "selection"]
    assert record.actions == [{
        "id": "lookup",
        "label": "词典",
        "title": "用中医辞典查所选文字",
        "context": "selection",
        "order": 40,
        "maxChars": 60,
        "stripPunctuation": True,
    }]
    assert record.window["width"] == 980
    assert record.url == "/plugins/dict/index.html"
    assert record.problem is None


def test_missing_manifest_is_listed_as_broken(registry):
    (registry.bundled_root / "broken").mkdir(parents=True)
    record = registry.get("broken")
    assert record.problem and "plugin.json" in record.problem
    # 坏的插件不进选区菜单，也不影响其它插件
    assert registry.selection_actions() == []


def test_entry_escaping_directory_is_rejected(registry):
    manifest = dict(VALID_MANIFEST, entry="../outside.html")
    write_plugin(registry.bundled_root, manifest=manifest)
    record = registry.get("dict")
    assert record.problem and "跳出插件目录" in record.problem


def test_absolute_entry_is_rejected(registry):
    manifest = dict(VALID_MANIFEST, entry="C:/Windows/System32/drivers/etc/hosts")
    write_plugin(registry.bundled_root, manifest=manifest)
    assert registry.get("dict").problem


def test_entry_must_be_html(registry):
    manifest = dict(VALID_MANIFEST, entry="data/ciku.json")
    write_plugin(registry.bundled_root, manifest=manifest)
    assert "html" in (registry.get("dict").problem or "")


def test_manifest_id_must_match_directory(registry):
    manifest = dict(VALID_MANIFEST, id="other")
    write_plugin(registry.bundled_root, manifest=manifest)
    assert "不一致" in (registry.get("dict").problem or "")


def test_directory_name_must_be_valid_id(registry):
    write_plugin(registry.bundled_root, plugin_id="Bad_ID")
    record = registry.get("Bad_ID")
    assert record.problem and "合法插件 id" in record.problem


def test_action_order_is_clamped_and_sorted(registry):
    manifest = dict(
        VALID_MANIFEST,
        selection={"actions": [
            {"id": "b", "label": "乙", "order": 500},
            {"id": "a", "label": "甲", "order": -3},
        ]},
    )
    write_plugin(registry.bundled_root, manifest=manifest)
    actions = registry.get("dict").actions
    assert [action["id"] for action in actions] == ["a", "b"]
    assert actions[0]["order"] == 0 and actions[1]["order"] == 99


def test_duplicate_action_ids_rejected(registry):
    manifest = dict(
        VALID_MANIFEST,
        selection={"actions": [
            {"id": "x", "label": "甲"},
            {"id": "x", "label": "乙"},
        ]},
    )
    write_plugin(registry.bundled_root, manifest=manifest)
    assert "重复" in (registry.get("dict").problem or "")


def test_unknown_surface_rejected(registry):
    write_plugin(registry.bundled_root, manifest=dict(VALID_MANIFEST, surface=["hologram"]))
    assert "surface" in (registry.get("dict").problem or "")


# --------------------------------------------------------------- 双根与启停


def test_user_root_overrides_bundled(registry):
    write_plugin(registry.bundled_root)
    registry.user_root.mkdir(parents=True, exist_ok=True)
    write_plugin(registry.user_root, manifest=dict(VALID_MANIFEST, version="2.0.0"))
    record = registry.get("dict")
    assert record.version == "2.0.0"
    assert record.source == "user"
    assert record.removable is True


def test_bundled_plugin_is_not_removable(registry):
    write_plugin(registry.bundled_root)
    record = registry.get("dict")
    assert record.removable is False
    with pytest.raises(PluginError):
        registry.uninstall("dict")


def test_toggle_persists_across_instances(registry):
    write_plugin(registry.bundled_root)
    registry.set_enabled("dict", False)
    assert (registry.data_dir / "plugins-state.json").is_file()
    fresh = PluginRegistry(
        registry.data_dir, bundled_root=registry.bundled_root, user_root=registry.user_root
    )
    assert fresh.get("dict").enabled is False
    # 停用后默认不再出现在选区动作里
    assert fresh.selection_actions() == []
    assert [a["id"] for a in fresh.selection_actions(include_disabled=True)] == ["lookup"]


def test_selection_actions_sort_by_order_then_plugin(registry):
    write_plugin(registry.bundled_root)
    write_plugin(
        registry.bundled_root,
        plugin_id="pinyin",
        manifest=dict(VALID_MANIFEST, id="pinyin", name="拼音标注",
                      selection={"actions": [{"id": "annotate", "label": "拼音", "order": 45}]}),
    )
    actions = registry.selection_actions()
    assert [(a["plugin"], a["id"]) for a in actions] == [("dict", "lookup"), ("pinyin", "annotate")]


# --------------------------------------------------------------- 静态资源


def test_asset_serves_whitelisted_files(registry):
    write_plugin(registry.bundled_root)
    target, content_type = registry.asset("dict", "data/ciku.json")
    assert target.name == "ciku.json" and content_type.startswith("application/json")


@pytest.mark.parametrize("relative", [
    "../user/dict/index.html",
    "../../etc/passwd",
    "/etc/passwd",
    ".env",
    "data/../../plugin.json.bak",
])
def test_asset_rejects_traversal_and_dotfiles(registry, relative):
    write_plugin(registry.bundled_root)
    with pytest.raises(PluginNotFound):
        registry.asset("dict", relative)


def test_asset_rejects_non_whitelisted_suffix(registry):
    write_plugin(registry.bundled_root)
    (registry.bundled_root / "dict" / "run.exe").write_bytes(b"MZ")
    with pytest.raises(PluginNotFound):
        registry.asset("dict", "run.exe")


def test_asset_unknown_plugin(registry):
    with pytest.raises(PluginNotFound):
        registry.asset("nope", "index.html")


# --------------------------------------------------------------- 安装 / 卸载


def make_zip(entries: dict[str, bytes], top: str | None = "dict") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, payload in entries.items():
            bundle.writestr(f"{top}/{name}" if top else name, payload)
    return buffer.getvalue()


def issue_zip(manifest=None, top="dict") -> bytes:
    payload = dict(VALID_MANIFEST) if manifest is None else manifest
    return make_zip({
        "plugin.json": json.dumps(payload, ensure_ascii=False).encode(),
        "index.html": b"<html>zip</html>",
        "icon.svg": b"<svg/>",
    }, top=top)


def test_install_zip_lands_in_user_root(registry):
    record = registry.install_zip(issue_zip())
    assert record.source == "user"
    assert (registry.user_root / "dict" / "index.html").is_file()


def test_install_zip_without_wrapper_directory(registry):
    record = registry.install_zip(issue_zip(top=None))
    assert record.id == "dict"


def test_install_zip_rejects_traversal_entries(registry):
    payload = make_zip({"plugin.json": b"{}", "../../evil.html": b"x"})
    with pytest.raises(PluginError):
        registry.install_zip(payload)


def test_install_zip_rejects_symlink_entries(registry):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        info = zipfile.ZipInfo("dict/link")
        info.external_attr = (0o120777 << 16)
        bundle.writestr(info, "target")
    with pytest.raises(PluginError):
        registry.install_zip(buffer.getvalue())


def test_install_zip_rejects_broken_manifest(registry):
    bad = dict(VALID_MANIFEST, entry="missing.html")
    with pytest.raises(PluginError):
        registry.install_zip(issue_zip(bad))


def test_install_zip_rejects_non_zip(registry):
    with pytest.raises(PluginError):
        registry.install_zip(b"not a zip at all")


def test_reinstall_trashes_previous_version(registry):
    registry.install_zip(issue_zip())
    registry.install_zip(issue_zip(dict(VALID_MANIFEST, version="1.1.0")))
    assert registry.get("dict").version == "1.1.0"
    trash = registry.data_dir / "plugins-trash"
    assert trash.is_dir() and len(list(trash.iterdir())) == 1


def test_uninstall_moves_to_trash(registry):
    registry.install_zip(issue_zip())
    trashed = registry.uninstall("dict")
    assert trashed.is_dir() and (trashed / "index.html").is_file()
    assert not (registry.user_root / "dict").exists()
    with pytest.raises(PluginNotFound):
        registry.get("dict")


def test_uninstall_clears_enabled_state(registry):
    registry.install_zip(issue_zip())
    registry.set_enabled("dict", False)
    registry.uninstall("dict")
    state = json.loads((registry.data_dir / "plugins-state.json").read_text(encoding="utf-8"))
    assert "dict" not in state["plugins"]


# --------------------------------------------------------------- 选区文本清洗


def test_clean_text_trims_and_truncates():
    assert clean_selection_text("  「本虚标实」  ", 60, False) == "「本虚标实」"
    assert clean_selection_text("本虚标实，", 60, True) == "本虚标实"
    assert clean_selection_text("《中风》", 60, True) == "中风"
    assert clean_selection_text("一二三四五", 3) == "一二三"


def test_clean_text_keeps_inner_punctuation():
    assert clean_selection_text("太阳病，脉浮", 60, True) == "太阳病，脉浮"


def test_clean_text_empty_input():
    assert clean_selection_text(None) == ""
    assert clean_selection_text("   ") == ""
