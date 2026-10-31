"""插件与技能种子的 HTTP 接线：/api/plugins、/plugins/<id>/…、selection-actions、
资料库挂载、恢复内置知识包。"""

from __future__ import annotations

import io
import json
import threading
import zipfile
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from reader_service.plugins import PluginRegistry
from reader_service.server import ReaderServer, handler_factory
from reader_service.skills import SkillRegistry

TOKEN = "test-plugins-token"

MANIFEST = {
    "id": "dict",
    "name": "中医辞典",
    "version": "1.0.0",
    "description": "九部离线词典",
    "entry": "index.html",
    "icon": "icon.svg",
    "surface": ["window", "selection"],
    "selection": {"actions": [{"id": "lookup", "label": "词典", "order": 40}]},
}


def write_plugin(root, manifest=None):
    payload = dict(MANIFEST) if manifest is None else manifest
    directory = root / payload["id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "index.html").write_text("<html>词典</html>", encoding="utf-8")
    (directory / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (directory / "data").mkdir(exist_ok=True)
    (directory / "data" / "ciku.json").write_text("{}", encoding="utf-8")
    return directory


def plugin_zip(manifest=None) -> bytes:
    payload = dict(MANIFEST) if manifest is None else manifest
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            "dict/plugin.json", json.dumps(payload, ensure_ascii=False).encode()
        )
        bundle.writestr("dict/index.html", b"<html>zip</html>")
        bundle.writestr("dict/icon.svg", b"<svg/>")
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, service):
    bundled = tmp_path / "bundled-plugins"
    user = tmp_path / "user-plugins"
    bundled.mkdir()
    registry = PluginRegistry(service.paths.root, bundled_root=bundled, user_root=user)
    skills = SkillRegistry(service.paths.root)
    seed_root = tmp_path / "default-skills"
    (seed_root / "yixue-zonghe").mkdir(parents=True)
    (seed_root / "yixue-zonghe" / "SKILL.md").write_text(
        "---\nname: yixue-zonghe\ndescription: 中医学习包\n---\n\n## 正文\n内容\n",
        encoding="utf-8",
    )
    return {
        "service": service,
        "plugins": registry,
        "skills": skills,
        "seed_root": seed_root,
        "bundled": bundled,
    }


@contextmanager
def running(env):
    server = ReaderServer(
        ("127.0.0.1", 0),
        handler_factory(
            env["service"],
            TOKEN,
            skills=env["skills"],
            plugins=env["plugins"],
            seed_source=str(env["seed_root"]),
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def call(url, *, method="GET", payload=None, body=None, content_type="application/json"):
    data = body if body is not None else (
        None if payload is None else json.dumps(payload).encode()
    )
    request = Request(
        url,
        data=data,
        method=method,
        headers={"X-Reader-Token": TOKEN, "Content-Type": content_type},
    )
    try:
        with urlopen(request) as response:
            raw = response.read()
            if not raw:
                return response.status, None
            return response.status, json.loads(raw)
    except HTTPError as error:
        raw = error.read()
        return error.code, (json.loads(raw) if raw else None)


# --------------------------------------------------------------- 列表与启停


def test_list_plugins(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        status, body = call(f"{base}/api/plugins")
    assert status == 200
    assert [item["id"] for item in body["plugins"]] == ["dict"]
    assert body["plugins"][0]["url"] == "/plugins/dict/index.html"
    assert body["plugins"][0]["removable"] is False
    assert body["plugins"][0]["selection"]["actions"] if "selection" in body["plugins"][0] else True


def test_selection_actions_hide_disabled_plugin(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        status, body = call(f"{base}/api/selection-actions")
        assert status == 200 and [a["id"] for a in body["actions"]] == ["lookup"]

        status, body = call(
            f"{base}/api/plugins/dict/enabled", method="PUT", payload={"enabled": False}
        )
        assert status == 200 and body["plugin"]["enabled"] is False

        status, body = call(f"{base}/api/selection-actions")
        assert status == 200 and body["actions"] == []


def test_toggle_requires_boolean(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        status, _ = call(
            f"{base}/api/plugins/dict/enabled", method="PUT", payload={"enabled": "no"}
        )
    assert status == 400


def test_toggle_unknown_plugin_is_404(env):
    with running(env) as base:
        status, _ = call(
            f"{base}/api/plugins/dict/enabled", method="PUT", payload={"enabled": True}
        )
    assert status == 404


# --------------------------------------------------------------- 静态资源


def test_plugin_assets_are_served(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        request = Request(f"{base}/plugins/dict/index.html", headers={"X-Reader-Token": TOKEN})
        with urlopen(request) as response:
            assert response.status == 200
            assert "词典" in response.read().decode()
            assert "text/html" in response.headers["Content-Type"]


def test_plugin_assets_reject_dotdot(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        status, _ = call(f"{base}/plugins/dict/../../plugin.json")
        assert status == 404


def test_plugin_assets_require_token(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        request = Request(f"{base}/plugins/dict/index.html")
        try:
            with urlopen(request):
                raise AssertionError("应当拒绝未带 token 的请求")
        except HTTPError as error:
            assert error.code in (401, 403)


# --------------------------------------------------------------- 安装 / 卸载


def test_install_zip_over_http(env):
    with running(env) as base:
        status, body = call(
            f"{base}/api/plugins/install",
            method="POST",
            body=plugin_zip(),
            content_type="application/zip",
        )
        assert status == 201, body
        assert body["plugin"]["source"] == "user"
        assert body["plugin"]["removable"] is True
        assert (env["plugins"].user_root / "dict" / "index.html").is_file()


def test_install_rejects_broken_package(env):
    with running(env) as base:
        status, body = call(
            f"{base}/api/plugins/install",
            method="POST",
            body=b"not a zip",
            content_type="application/zip",
        )
    assert status == 400 and body["code"] == "INVALID_PLUGIN_PACKAGE"


def test_uninstall_moves_to_trash(env):
    with running(env) as base:
        call(
            f"{base}/api/plugins/install",
            method="POST",
            body=plugin_zip(),
            content_type="application/zip",
        )
        status, body = call(f"{base}/api/plugins/dict", method="DELETE")
        assert status == 200
        assert body["trashed"].endswith("dict")
        from pathlib import Path

        assert Path(body["trashed"]).is_dir()


def test_uninstall_bundled_plugin_is_conflict(env):
    write_plugin(env["bundled"])
    with running(env) as base:
        status, body = call(f"{base}/api/plugins/dict", method="DELETE")
    assert status == 409 and body["code"] == "PLUGIN_UNINSTALL_FAILED"


# --------------------------------------------------------------- 技能种子与资料库


def test_skills_list_marks_seeded_and_reports_size(env):
    from reader_service.skills import seed_default_skills

    seed_default_skills(env["service"].paths.root, env["seed_root"])
    with running(env) as base:
        status, body = call(f"{base}/api/skills")
    assert status == 200
    record = next(item for item in body["skills"] if item["name"] == "yixue-zonghe")
    assert record["seeded"] is True
    assert record["size"] > 0 and record["size_human"].endswith(("B", "KB", "MB"))
    assert record["library"]["mounted"] is False


def test_library_mount_and_unmount(env, tmp_path):
    from reader_service.skills import seed_default_skills

    seed_default_skills(env["service"].paths.root, env["seed_root"])
    target = tmp_path / "full-skill"
    (target / "中医教材").mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: yixue-zonghe\n---\n\n正文\n", encoding="utf-8")
    (target / "中医教材" / "a.txt").write_text("教材", encoding="utf-8")
    with running(env) as base:
        status, body = call(
            f"{base}/api/skills/yixue-zonghe/library",
            method="PUT",
            payload={"path": str(target)},
        )
        assert status == 200, body
        assert body["library"]["mounted"] is True
        assert body["library"]["textbooks"] == [{"name": "中医教材", "files": 1}]

        status, body = call(f"{base}/api/skills/yixue-zonghe/library", method="DELETE")
        assert status == 200 and body["library"]["mounted"] is False
    assert (target / "SKILL.md").is_file()


def test_library_mount_rejects_non_skill_directory(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with running(env) as base:
        status, body = call(
            f"{base}/api/skills/yixue-zonghe/library",
            method="PUT",
            payload={"path": str(plain)},
        )
    assert status == 400 and body["code"] == "INVALID_LIBRARY_PATH"


def test_restore_builtin_skill(env):
    target = env["service"].paths.root / "skills" / "yixue-zonghe"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("坏掉的正文", encoding="utf-8")
    with running(env) as base:
        status, body = call(f"{base}/api/skills/yixue-zonghe/restore", method="POST")
    assert status == 200, body
    assert body["skill"]["seeded"] is True
    assert "正文" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_restore_unknown_skill_is_404(env):
    with running(env) as base:
        status, _ = call(f"{base}/api/skills/nope/restore", method="POST")
    assert status == 404


def test_restore_keeps_previous_version_in_trash(env):
    """恢复内置知识包是"先建新的、再把旧的挪走"：旧版进 skills-trash，不是删掉。"""
    target = env["service"].paths.root / "skills" / "yixue-zonghe"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("坏掉的正文", encoding="utf-8")
    with running(env) as base:
        status, body = call(f"{base}/api/skills/yixue-zonghe/restore", method="POST")
    assert status == 200, body
    trash = env["service"].paths.root / "skills-trash"
    kept = list(trash.glob("*-yixue-zonghe"))
    assert len(kept) == 1
    assert (kept[0] / "SKILL.md").read_text(encoding="utf-8") == "坏掉的正文"
    assert "正文" in (target / "SKILL.md").read_text(encoding="utf-8")
    # 暂存目录不留垃圾
    assert not list((env["service"].paths.root / "skills").glob(".*.restoring"))


def test_restore_keeps_previous_version_in_trash(env):
    """恢复内置知识包是"先建新的、再把旧的挪走"：旧版进 skills-trash，不是删掉。"""
    target = env["service"].paths.root / "skills" / "yixue-zonghe"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("坏掉的正文", encoding="utf-8")
    with running(env) as base:
        status, body = call(f"{base}/api/skills/yixue-zonghe/restore", method="POST")
    assert status == 200, body
    trash = env["service"].paths.root / "skills-trash"
    kept = list(trash.glob("*-yixue-zonghe"))
    assert len(kept) == 1
    assert (kept[0] / "SKILL.md").read_text(encoding="utf-8") == "坏掉的正文"
    assert "正文" in (target / "SKILL.md").read_text(encoding="utf-8")
    # 暂存目录不留垃圾
    assert not list((env["service"].paths.root / "skills").glob(".*.restoring"))
