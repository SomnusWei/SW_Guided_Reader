"""随包插件（dict / pinyin）的 manifest 与页面契约护栏（§3.1 / §3.2 / §6.2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reader_service.plugins import PluginRegistry

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    if not PLUGINS.is_dir():
        pytest.skip("仓库里没有 plugins/")
    root = tmp_path_factory.mktemp("plugin-registry")
    return PluginRegistry(root / "data", bundled_root=PLUGINS, user_root=root / "user")


def read(plugin_id: str, name: str) -> str:
    path = PLUGINS / plugin_id / name
    if not path.is_file():
        pytest.skip(f"缺少 {path}（词典数据不随 git）")
    return path.read_text(encoding="utf-8")


def test_both_bundled_plugins_load_without_problem(registry):
    records = {record.id: record for record in registry.list()}
    assert set(records) >= {"dict", "pinyin"}
    for plugin_id in ("dict", "pinyin"):
        record = records[plugin_id]
        assert record.problem is None, f"{plugin_id} manifest 有问题：{record.problem}"
        assert record.source == "bundled"
        assert record.removable is False
        assert record.entry == "index.html"
        assert record.icon == "icon.svg"
        assert set(record.surfaces) == {"window", "selection"}


def test_selection_actions_order_matches_plan(registry):
    actions = {action["plugin"]: action for action in registry.selection_actions()}
    assert actions["dict"]["id"] == "lookup"
    assert actions["dict"]["label"] == "词典"
    assert actions["dict"]["order"] == 40
    assert actions["dict"]["maxChars"] == 60
    assert actions["dict"]["stripPunctuation"] is True

    assert actions["pinyin"]["id"] == "annotate"
    assert actions["pinyin"]["label"] == "拼音"
    assert actions["pinyin"]["order"] == 45
    assert actions["pinyin"]["maxChars"] == 300
    assert actions["pinyin"]["stripPunctuation"] is False


@pytest.mark.parametrize("plugin_id", ["dict", "pinyin"])
def test_manifest_declares_window_size_and_category(plugin_id):
    manifest = json.loads((PLUGINS / plugin_id / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == 1
    assert manifest["window"]["title"]
    assert 320 <= manifest["window"]["width"] <= 4096
    assert manifest["category"]
    assert manifest["permissions"] == ["read-own-assets"]


@pytest.mark.parametrize("plugin_id", ["dict", "pinyin"])
def test_plugin_page_honours_reader_contract(plugin_id):
    html = read(plugin_id, "index.html")
    # §3.2：读 URL 参数（首屏出结果）+ 接收 lt-plugin-context（二次触发）
    assert "URLSearchParams(location.search)" in html
    assert "lt-plugin-context" in html
    assert "addEventListener('message'" in html
    assert "data-theme" in html


def test_dict_page_prefills_from_text_param(registry):
    html = read("dict", "index.html")
    # ?text= 要走与 ?q= 同一条检索路径，否则弹窗打开是空白页
    assert "usp.get('q') || usp.get('text')" in html
    assert "在书中搜此词" in html
    assert "search-in-book" in html


def test_dict_data_and_index_exist():
    data = PLUGINS / "dict" / "data"
    if not data.is_dir():
        pytest.skip("词典数据未同步（plugins/dict/data 不入 git）")
    index = data / "index.json"
    assert index.is_file(), "缺少 data/index.json，词典页会载入失败"
    payload = json.loads(index.read_text(encoding="utf-8"))
    assert payload, "index.json 为空"


def test_pinyin_page_renders_ruby_and_copies_three_formats():
    html = read("pinyin", "index.html")
    assert "<rt>" in html or "renderRuby" in html
    for label in ("复制 ruby", "复制上标版", "复制纯文本"):
        assert label in html, f"缺少复制格式：{label}"
    # 纠音表内置、用户追加只写 localStorage（不写插件目录）
    assert "荥" in html and "膻中" in html
    assert "localStorage" in html


def test_pinyin_page_has_no_remote_fallback():
    """插件页不能依赖外网：原页面的 jsdelivr 兜底必须去掉。"""
    html = read("pinyin", "index.html")
    for needle in ("jsdelivr", "unpkg", "cdn."):
        assert needle not in html, f"拼音插件仍引用外网资源：{needle}"


def test_plugin_assets_are_all_whitelisted(registry):
    """插件目录里不该出现静态服务白名单之外的文件类型（会被 404）。"""
    from reader_service.plugins.registry import ASSET_SUFFIXES

    for plugin_id in ("dict", "pinyin"):
        root = PLUGINS / plugin_id
        if not root.is_dir():
            pytest.skip("插件目录不存在")
        offenders = {
            path.suffix.lower()
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() not in ASSET_SUFFIXES
        }
        assert offenders == set(), f"{plugin_id} 里有不安全后缀：{offenders}"
