"""HTTP wiring for annotations, skills, markdown export, outline bookmarks,
providers and the Assistant skill selection."""

from __future__ import annotations

import io
import json
import os
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from reader_service.agent_runtime import CredentialRead
from reader_service.annotation import AnnotationSyncConfig, PdfAnnotationSync
from reader_service.assistant import AssistantService
from reader_service.assistant.service import SYSTEM_MESSAGE
from reader_service.export_md import MarkdownExporter
from reader_service.skills import BUILTIN_SKILL_NAME, SkillInstaller, SkillRegistry
from reader_service.server import ReaderServer, handler_factory

from test_annotations import prepared_services
from test_ask_about_this import MockAdapter, assistant_fixture, runtime, selection  # noqa: F401
from test_outline_bookmarks import bookmarked_pdf_bytes, outline_service

TOKEN = "test-wiring-token"

DEMO_SKILL = """---
name: demo-skill
description: 用于接线测试的演示技能
trigger_words: 总线, 事务
---

## 演示技能

技能标记-WIRING-9F2。
"""


@contextmanager
def running_server(
    service,
    *,
    annotations=None,
    outline=None,
    assistant=None,
    annotation_sync=None,
    pdf_sync=None,
    skills=None,
    exporter=None,
):
    server = ReaderServer(
        ("127.0.0.1", 0),
        handler_factory(
            service,
            TOKEN,
            annotations=annotations,
            outline=outline,
            assistant=assistant,
            annotation_sync=annotation_sync,
            pdf_sync=pdf_sync,
            skills=skills,
            exporter=exporter,
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


def request_json(url, *, method="GET", payload=None, token=TOKEN, headers=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=body,
        method=method,
        headers={"X-Reader-Token": token, "Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request) as response:
        if response.status == 204:
            return response.status, None
        return response.status, json.load(response)


def status_of(url, *, method="GET", payload=None):
    try:
        return request_json(url, method=method, payload=payload)[0]
    except HTTPError as error:
        return error.code, json.load(error)


def write_skill_package(root, name="demo-skill", document=DEMO_SKILL):
    package = root / f"{name}-source"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(document, encoding="utf-8")
    return package


# --------------------------------------------------------------- 批注路由


def test_annotation_routes_create_every_kind_and_update(service):
    _book, revision, annotations = prepared_services(service)
    revision_id = revision["id"]
    text_selection = {
        "start": {"line_ordinal": 0, "boundary": 1},
        "end": {"line_ordinal": 1, "boundary": 2},
    }
    with running_server(service, annotations=annotations) as base:
        created = {}
        for kind, extra in (
            ("HIGHLIGHT", {"highlight_style": "YELLOW"}),
            ("UNDERLINE", {"highlight_style": "GREEN", "body": "强调"}),
            ("TEXTBOX", {"rect": [0.1, 0.3, 0.5, 0.4], "body": "文本框笔记"}),
            ("REGION", {"rect": [0.2, 0.5, 0.6, 0.7]}),
        ):
            payload = {"kind": kind, "pdf_page_index": 0, **extra}
            if kind in ("HIGHLIGHT", "UNDERLINE"):
                payload.update(text_selection)
            status, body = request_json(
                f"{base}/api/revisions/{revision_id}/annotations", method="POST", payload=payload
            )
            assert status == 201, (kind, body)
            assert body["annotation"]["kind"] == kind
            created[kind] = body["annotation"]

        status, body = status_of(
            f"{base}/api/revisions/{revision_id}/annotations",
            method="POST",
            payload={"kind": "AI_NOTE", "pdf_page_index": 0, **text_selection},
        )
        assert status == 400 and "Assistant" in body["error"]

        status, body = status_of(
            f"{base}/api/revisions/{revision_id}/annotations",
            method="POST",
            payload={"kind": "BOGUS", "pdf_page_index": 0, **text_selection},
        )
        assert status == 400

        # 前端契约：page_index + color 别名，且未带 pdf_page_index/highlight_style
        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations",
            method="POST",
            payload={
                "kind": "TEXTBOX",
                "page_index": 0,
                "rect": [0.3, 0.3, 0.6, 0.5],
                "color": "GREEN",
                "body": "别名创建",
            },
        )
        assert status == 201
        assert body["annotation"]["highlight_style"] == "GREEN"
        created["ALIAS"] = body["annotation"]

        status, body = request_json(f"{base}/api/revisions/{revision_id}/annotations?page=0")
        assert status == 200 and len(body["annotations"]) == 5
        status, body = request_json(f"{base}/api/revisions/{revision_id}/annotations")
        assert status == 200
        assert [item["pdf_page_index"] for item in body["annotations"]] == [0, 0, 0, 0, 0]

        textbox_id = created["TEXTBOX"]["id"]
        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations/{textbox_id}",
            method="PATCH",
            payload={"body": "改过的笔记", "color": "PURPLE"},
        )
        assert status == 200
        assert body["annotation"]["body"] == "改过的笔记"
        assert body["annotation"]["highlight_style"] == "PURPLE"

        status, _body = status_of(
            f"{base}/api/revisions/{revision_id}/annotations/{created['HIGHLIGHT']['id']}",
            method="PATCH",
            payload={"rect": [0.1, 0.1, 0.2, 0.2]},
        )
        assert status == 400

        status, _body = status_of(
            f"{base}/api/revisions/{revision_id}/annotations/{textbox_id}",
            method="PATCH",
            payload=None,
        )
        assert status == 400

        status, _body = status_of(
            f"{base}/api/revisions/{revision_id}/annotations/00000000-0000-0000-0000-000000000000",
            method="PATCH",
            payload={"body": "x"},
        )
        assert status == 404

        status, _body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations/{created['REGION']['id']}",
            method="DELETE",
        )
        assert status == 204
        status, body = request_json(f"{base}/api/revisions/{revision_id}/annotations")
        assert len(body["annotations"]) == 4
        status, _body = status_of(
            f"{base}/api/revisions/{revision_id}/annotations/{created['REGION']['id']}",
            method="DELETE",
        )
        assert status == 404


# ------------------------------------------------- 批注写回设置与手动写回


def test_annotation_sync_settings_and_manual_writeback(service):
    _book, revision, annotations = prepared_services(service)
    revision_id = revision["id"]
    pdf_sync = PdfAnnotationSync()
    with running_server(
        service,
        annotations=annotations,
        annotation_sync=AnnotationSyncConfig(mode="manual", target="source"),
        pdf_sync=pdf_sync,
    ) as base:
        status, body = request_json(f"{base}/api/settings/annotation-sync")
        assert status == 200
        assert body["mode"] == "manual" and body["target"] == "source"
        assert body["available"] is True

        status, _body = status_of(
            f"{base}/api/settings/annotation-sync",
            method="PUT",
            payload={"mode": "sometimes"},
        )
        assert status == 400

        status, body = request_json(
            f"{base}/api/settings/annotation-sync",
            method="PUT",
            payload={"mode": "AUTO", "target": "COPY"},
        )
        assert status == 200 and body == {"mode": "auto", "target": "copy"}
        status, body = request_json(f"{base}/api/settings/annotation-sync")
        assert body["mode"] == "auto" and body["target"] == "copy"

        status, body = request_json(
            f"{base}/api/settings/annotation-sync",
            method="PUT",
            payload={"mode": "auto", "target": "copy"},
        )
        assert status == 200 and body == {"mode": "auto", "target": "copy"}
        stored = json.loads((service.paths.root / "config.json").read_text(encoding="utf-8"))
        assert stored["annotation_pdf_sync"] == "auto"
        assert stored["annotation_pdf_target"] == "copy"

        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations",
            method="POST",
            payload={
                "kind": "TEXTBOX",
                "pdf_page_index": 0,
                "rect": [0.1, 0.3, 0.5, 0.4],
                "body": "写回测试",
            },
        )
        assert status == 201
        assert body["pdf_sync"] == "pending"
        annotation_id = body["annotation"]["id"]

        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations/sync-all", method="POST"
        )
        assert status == 200
        assert body["pdf_sync"] == "synced"
        assert body["target"] == "copy"
        assert body["written"] >= 1
        assert body["path"].endswith("-annotated.pdf")

        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations/{annotation_id}/sync",
            method="POST",
            payload={},
        )
        assert status == 200
        assert body["annotation_id"] == annotation_id
        assert body["written"] >= 1

        status, body = request_json(
            f"{base}/api/settings/annotation-sync",
            method="PUT",
            payload={"target": "source"},
        )
        assert status == 200 and body["target"] == "source"
        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/annotations/sync-all", method="POST"
        )
        assert status == 200
        assert body["target"] == "source"
        assert body["written"] >= 1
        backups = list((service.paths.root / "annotation-backups").glob("*.pdf.bak"))
        assert backups
        with urlopen(
            Request(
                f"{base}/api/revisions/{revision_id}/pdf",
                headers={"X-Reader-Token": TOKEN},
            )
        ) as response:
            assert response.read(4) == b"%PDF"


def test_annotation_sync_disabled_reports_unavailable(service):
    _book, revision, annotations = prepared_services(service)
    with running_server(service, annotations=annotations) as base:
        status, body = request_json(f"{base}/api/settings/annotation-sync")
        assert status == 200 and body["available"] is False
        status, body = status_of(
            f"{base}/api/settings/annotation-sync", method="PUT", payload={"mode": "auto"}
        )
        assert status == 503
        status, body = status_of(
            f"{base}/api/revisions/{revision['id']}/annotations/sync-all", method="POST"
        )
        assert status == 503 and body["code"] == "PDF_SYNC_UNAVAILABLE"


# --------------------------------------------------------------- 技能路由


def test_skill_routes_install_toggle_and_uninstall(service, tmp_path):
    skills = SkillRegistry(service.paths.root)
    package = write_skill_package(tmp_path)
    with running_server(service, skills=skills) as base:
        status, body = request_json(f"{base}/api/skills")
        assert status == 200
        builtin = [item for item in body["skills"] if item["name"] == BUILTIN_SKILL_NAME]
        assert builtin and builtin[0]["is_default"] is True

        status, body = request_json(
            f"{base}/api/skills/install", method="POST", payload={"path": str(package)}
        )
        assert status == 201 and body["skill"]["name"] == "demo-skill"

        status, body = request_json(f"{base}/api/skills")
        names = {item["name"]: item for item in body["skills"]}
        assert names["demo-skill"]["enabled"] is True

        status, body = request_json(
            f"{base}/api/skills/demo-skill/enabled", method="PUT", payload={"enabled": False}
        )
        assert status == 200 and body == {"name": "demo-skill", "enabled": False}
        status, body = request_json(f"{base}/api/skills")
        assert {item["name"]: item for item in body["skills"]}["demo-skill"]["enabled"] is False

        status, body = status_of(
            f"{base}/api/skills/demo-skill/enabled", method="PUT", payload={"enabled": "no"}
        )
        assert status == 400
        status, body = status_of(
            f"{base}/api/skills/{BUILTIN_SKILL_NAME}/enabled",
            method="PUT",
            payload={"enabled": False},
        )
        assert status == 409
        status, body = status_of(
            f"{base}/api/skills/ghost/enabled", method="PUT", payload={"enabled": True}
        )
        assert status == 404

        empty = tmp_path / "empty-package"
        empty.mkdir()
        status, body = status_of(
            f"{base}/api/skills/install", method="POST", payload={"source_dir": str(empty)}
        )
        assert status == 400
        assert "非法技能包" in body["error"]

        status, _body = request_json(f"{base}/api/skills/demo-skill", method="DELETE")
        assert status == 204
        status, body = status_of(f"{base}/api/skills/demo-skill", method="DELETE")
        assert status == 404
        status, body = status_of(f"{base}/api/skills/{BUILTIN_SKILL_NAME}", method="DELETE")
        assert status == 409


# --------------------------------------------------------------- Markdown 导出


def test_markdown_export_json_and_download(service, tmp_path, monkeypatch):
    _book, revision, annotations = prepared_services(service)
    revision_id = revision["id"]
    annotations.create(
        revision_id,
        kind="TEXTBOX",
        page_index=0,
        rect=[0.1, 0.3, 0.5, 0.4],
        body="导出用的笔记",
    )
    exporter = MarkdownExporter(service.database, service.paths)
    with running_server(service, annotations=annotations, exporter=exporter) as base:
        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations"
        )
        assert status == 200
        assert body["filename"].endswith(".md")
        assert "批注汇总" in body["content"]
        assert "导出用的笔记" in body["content"]
        assert body["archived"] and body["archived"].endswith(".md")

        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=bundle&annotations=1"
        )
        assert status == 200 and "学习导出" in body["content"]

        request = Request(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations&download=1",
            headers={"X-Reader-Token": TOKEN},
        )
        with urlopen(request) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/markdown")
            disposition = response.headers["Content-Disposition"]
            encoded_name = disposition.split("filename*=UTF-8''", 1)[1]
            assert disposition.startswith("attachment; filename*=UTF-8''")
            assert encoded_name.startswith(quote(body["filename"], safe="")[:4])
            downloaded = response.read().decode("utf-8")
        assert "批注汇总" in downloaded

        # 桌面外壳（Tauri/WebView2 丢弃 blob 下载）走 save=1：服务端写系统「下载」文件夹
        fake_downloads = tmp_path / "Downloads"
        fake_downloads.mkdir()
        monkeypatch.setattr("reader_service.export_md.Path.home", lambda: tmp_path)
        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations&download=1&save=1"
        )
        assert status == 200 and body["saved"] is True
        saved = tmp_path / "Downloads" / body["filename"]
        assert saved.exists() and "批注汇总" in saved.read_text(encoding="utf-8")
        # 重名导出自动加序号，不覆盖
        status, body2 = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations&download=1&save=1"
        )
        assert status == 200 and body2["filename"] != body["filename"]

        # dialog=1：弹系统「另存为」让用户自选位置——模拟用户选定自定义路径
        from reader_service.native_dialog import DialogUnavailable
        chosen = tmp_path / "导出" / "自选位置.md"
        monkeypatch.setattr(
            "reader_service.server.ask_save_path",
            lambda filename, title="导出笔记": chosen,
        )
        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations&download=1&save=1&dialog=1"
        )
        assert status == 200 and body["saved"] is True and body["path"] == str(chosen)
        assert chosen.exists() and "批注汇总" in chosen.read_text(encoding="utf-8")

        # 用户在对话框里点「取消」→ cancelled，不写文件也不算错误
        monkeypatch.setattr(
            "reader_service.server.ask_save_path",
            lambda filename, title="导出笔记": None,
        )
        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations&download=1&save=1&dialog=1"
        )
        assert status == 200 and body == {"saved": False, "cancelled": True}

        # 环境不支持对话框（DialogUnavailable）→ 回落「下载」文件夹通道
        def _unavailable(filename, title="导出笔记"):
            raise DialogUnavailable("仅 Windows")
        monkeypatch.setattr("reader_service.server.ask_save_path", _unavailable)
        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}&scope=annotations&download=1&save=1&dialog=1"
        )
        assert status == 200 and body["saved"] is True
        assert (tmp_path / "Downloads" / body["filename"]).exists()

        status, body = status_of(
            f"{base}/api/export/markdown?revision={revision_id}&scope=bogus"
        )
        assert status == 400
        status, body = status_of(
            f"{base}/api/export/markdown?revision=00000000-0000-0000-0000-000000000000&scope=annotations"
        )
        assert status == 404


def test_markdown_export_assistant_conversation(service, assistant_fixture):
    adapter = MockAdapter(["总线事务由地址、数据和控制信号组成。"])
    assistant = AssistantService(assistant_fixture["contexts"], runtime(adapter))
    revision_id = assistant_fixture["revision_id"]
    state = assistant.ask_selection(
        "reader-session-export", revision_id, 3, **selection(3)
    )
    current = state["current"]
    exporter = MarkdownExporter(service.database, service.paths)
    with running_server(service, assistant=assistant, exporter=exporter) as base:
        status, body = request_json(
            f"{base}/api/export/markdown?revision={revision_id}"
            f"&scope=assistant_conversation&id={current['root_id']}"
        )
        assert status == 200
        assert "选段问答" in body["content"]
        assert "总线事务由地址、数据和控制信号组成。" in body["content"]
        assert body["archived"]

        status, _body = status_of(
            f"{base}/api/export/markdown?revision={revision_id}&scope=assistant_conversation"
        )
        assert status == 400
        status, _body = status_of(
            f"{base}/api/export/markdown?revision={revision_id}"
            f"&scope=assistant_conversation&id=00000000-0000-0000-0000-000000000000"
        )
        assert status == 404


# --------------------------------------------------------------- Provider 列表


def test_provider_catalog_reports_configured_state(service, monkeypatch):
    import reader_service.server as server_module

    def fake_credential(name):
        if name == "deepseek":
            return CredentialRead(True, "TEST", None, "secret")
        return CredentialRead(False, "TEST", "CREDENTIAL_NOT_FOUND")

    monkeypatch.setattr(server_module, "read_provider_credential", fake_credential)
    with running_server(service) as base:
        status, body = request_json(f"{base}/api/providers")
        assert status == 200
        providers = {item["name"]: item for item in body["providers"]}
        assert providers["deepseek"]["configured"] is True
        assert providers["zhipu"]["configured"] is False
        for item in providers.values():
            assert set(item) == {
                "name", "display_name", "endpoint", "default_model",
                "credential_target", "configured",
            }
            assert item["credential_target"] == f"408-guided-reader-{item['name']}"


# --------------------------------------------------------------- 模型密钥设置


def test_provider_key_routes_save_and_delete(service, monkeypatch):
    import reader_service.server as server_module

    stored: dict[str, str] = {}
    deleted: list[str] = []

    def fake_write(provider, api_key):
        if provider not in server_module.PROVIDER_PRESETS:
            raise ValueError("unknown provider")
        if not api_key.strip():
            raise ValueError("api_key 不能为空")
        stored[provider] = api_key.strip()

    def fake_delete(provider):
        if provider not in server_module.PROVIDER_PRESETS:
            raise ValueError("unknown provider")
        deleted.append(provider)
        stored.pop(provider, None)

    def fake_read(provider):
        if provider in stored:
            return CredentialRead(True, "TEST", None, stored[provider])
        return CredentialRead(False, "TEST", "CREDENTIAL_NOT_FOUND")

    monkeypatch.setattr(server_module, "write_provider_credential", fake_write)
    monkeypatch.setattr(server_module, "delete_provider_credential", fake_delete)
    monkeypatch.setattr(server_module, "read_provider_credential", fake_read)

    with running_server(service) as base:
        status, body = request_json(
            f"{base}/api/settings/providers/zhipu/key",
            method="PUT", payload={"api_key": "  zk-test-123  "},
        )
        assert status == 200 and body["configured"] is True
        assert body["credential_target"] == "408-guided-reader-zhipu"
        assert stored == {"zhipu": "zk-test-123"}

        status, catalog = request_json(f"{base}/api/providers")
        providers = {item["name"]: item for item in catalog["providers"]}
        assert providers["zhipu"]["configured"] is True

        status, body = status_of(
            f"{base}/api/settings/providers/zhipu/key",
            method="PUT", payload={"api_key": "   "},
        )
        assert status == 400 and "api_key" in body["error"]

        status, body = status_of(
            f"{base}/api/settings/providers/no-such/key",
            method="PUT", payload={"api_key": "x"},
        )
        assert status == 404

        status, _ = request_json(
            f"{base}/api/settings/providers/zhipu/key", method="DELETE"
        )
        assert status == 204
        assert deleted == ["zhipu"]
        status, catalog = request_json(f"{base}/api/providers")
        providers = {item["name"]: item for item in catalog["providers"]}
        assert providers["zhipu"]["configured"] is False


def test_provider_key_routes_require_token(service, monkeypatch):
    import reader_service.server as server_module

    monkeypatch.setattr(
        server_module,
        "write_provider_credential",
        lambda provider, api_key: None,
    )
    with running_server(service) as base:
        request = Request(
            f"{base}/api/settings/providers/zhipu/key",
            data=json.dumps({"api_key": "x"}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(request)
            raised = False
        except HTTPError as error:
            raised = error.code == 401
        assert raised


# --------------------------------------------------------------- 快捷键设置


def test_shortcuts_roundtrip_and_persist(service):
    with running_server(service) as base:
        status, body = request_json(f"{base}/api/settings/shortcuts")
        assert status == 200
        assert body["shortcuts"] == {}
        assert "highlight" in body["actions"]

        status, body = request_json(
            f"{base}/api/settings/shortcuts",
            method="PUT",
            payload={"shortcuts": {"highlight": "Ctrl+Alt+H", "ask_ai": "Ctrl+Q", "note": "  "}},
        )
        assert status == 200
        # 空白组合键视为解绑，不落盘
        assert body["shortcuts"] == {"highlight": "Ctrl+Alt+H", "ask_ai": "Ctrl+Q"}

        status, body = request_json(f"{base}/api/settings/shortcuts")
        assert status == 200
        assert body["shortcuts"] == {"highlight": "Ctrl+Alt+H", "ask_ai": "Ctrl+Q"}

    with running_server(service) as base:
        status, body = request_json(f"{base}/api/settings/shortcuts")
        assert status == 200
        assert body["shortcuts"] == {"highlight": "Ctrl+Alt+H", "ask_ai": "Ctrl+Q"}


def test_shortcuts_reject_unknown_action(service):
    with running_server(service) as base:
        status, body = status_of(
            f"{base}/api/settings/shortcuts",
            method="PUT",
            payload={"shortcuts": {"zoom_in": "Ctrl+K"}},
        )
        assert status == 400 and "zoom_in" in body["error"]

        status, body = status_of(
            f"{base}/api/settings/shortcuts",
            method="PUT",
            payload={"shortcuts": {"highlight": 123}},
        )
        assert status == 400 and "字符串" in body["error"]


# --------------------------------------------------------------- 书签大纲


def test_import_bookmarks_route_returns_statistics_and_sources(service):
    pdf = bookmarked_pdf_bytes()
    imported = service.intake(
        io.BytesIO(pdf), content_length=len(pdf), filename="bookmarked.pdf"
    )
    revision_id = imported["book"]["active_revision"]["id"]
    outline = outline_service(service)
    with running_server(service, outline=outline) as base:
        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/outline/import-bookmarks", method="POST"
        )
        assert status == 200
        assert body["imported"] >= 4
        assert body["max_depth"] >= 2
        assert isinstance(body["skipped"], int)
        assert all(node["source"] == "PDF_BOOKMARK" for node in body["nodes"])
        pages = {node["start_page"] for node in body["nodes"] if node["start_page"] is not None}
        assert pages and pages <= {0, 2, 3, 4, 5}

        status, body = request_json(f"{base}/api/revisions/{revision_id}/outline?stored=1")
        assert status == 200
        assert body["nodes"] and all("source" in node for node in body["nodes"])


# --------------------------------------------------- 启动/退出钩子


def test_launch_file_helpers_round_trip(tmp_path):
    from reader_service.__main__ import remove_launch_file, write_launch_file

    path = tmp_path / "reader-launch.json"
    write_launch_file(path, "http://127.0.0.1:8765/?token=abc")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["url"] == "http://127.0.0.1:8765/?token=abc"
    assert payload["pid"] == os.getpid()
    assert isinstance(payload["ts"], float)

    remove_launch_file(path)
    assert not path.exists()
    remove_launch_file(path)  # already gone is not an error


# --------------------------------------------------- Assistant 提问带技能


def test_assistant_ask_uses_selected_skill_and_rejects_unusable(service, assistant_fixture, tmp_path):
    SkillInstaller(service.paths.root).install_from_directory(write_skill_package(tmp_path))
    skills = SkillRegistry(service.paths.root)

    adapter = MockAdapter(["技能答", "默认答", "被拒答"])
    assistant = AssistantService(assistant_fixture["contexts"], runtime(adapter))
    revision_id = assistant_fixture["revision_id"]
    with running_server(service, assistant=assistant, skills=skills) as base:
        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/assistant/ask",
            method="POST",
            payload={
                "reader_session_id": "reader-session-wiring",
                "pdf_page_index": 3,
                "skill": "demo-skill",
                **selection(3),
            },
        )
        assert status == 200
        system_prompt = adapter.calls[-1]["body"]["messages"][0]["content"]
        assert "技能标记-WIRING-9F2" in system_prompt

        status, body = request_json(
            f"{base}/api/revisions/{revision_id}/assistant/ask",
            method="POST",
            payload={
                "reader_session_id": "reader-session-wiring",
                "pdf_page_index": 3,
                **selection(3),
            },
        )
        assert status == 200
        assert adapter.calls[-1]["body"]["messages"][0]["content"] == SYSTEM_MESSAGE

        status, body = status_of(
            f"{base}/api/revisions/{revision_id}/assistant/ask",
            method="POST",
            payload={
                "reader_session_id": "reader-session-wiring",
                "pdf_page_index": 3,
                "skill": "ghost-skill",
                **selection(3),
            },
        )
        assert status == 400 and body["code"] == "INVALID_ASK"

        skills.set_enabled("demo-skill", False)
        status, body = status_of(
            f"{base}/api/revisions/{revision_id}/assistant/ask",
            method="POST",
            payload={
                "reader_session_id": "reader-session-wiring",
                "pdf_page_index": 3,
                "skill": "demo-skill",
                **selection(3),
            },
        )
        assert status == 400 and "未启用" in body["error"]
