"""换书库目录（§5.3）：换目录 = 换"以后新书放哪"，不动任何已有文件。

核心不变量：读永远「外部书库 → 默认 blobs」两个根都找（旧书照常打开），
写永远落当前生效的目录（新书进新目录）；迁移是显式动作、同名跳过不覆盖。
"""
from __future__ import annotations

import hashlib

import pytest

from reader_service.storage import BlobStore, ManagedPaths, PathContainmentError


def _fake_pdf(paths: ManagedPaths, content: bytes) -> str:
    """走一遍真实的 intake 落盘：写临时文件 → commit，返回 sha256。"""
    sha = hashlib.sha256(content).hexdigest()
    tmp = paths.new_upload()[0]
    tmp.write_bytes(content)
    BlobStore(paths).commit(tmp, sha)
    return sha


def test_blob_override_routes_new_writes_and_keeps_old_reads(tmp_path):
    default_root = tmp_path / "data"
    paths = ManagedPaths(default_root)
    paths.initialize()
    old_sha = _fake_pdf(paths, b"old-book")

    external = tmp_path / "external-library"
    paths.set_blob_override(external)
    assert paths.blob_override() == external.resolve()

    new_sha = _fake_pdf(paths, b"new-book")
    # 新书落在外部书库
    assert (external / new_sha[:2] / f"{new_sha}.pdf").is_file()
    assert not (default_root / "blobs" / new_sha[:2] / f"{new_sha}.pdf").exists()
    # 旧书仍从默认 blobs 读到
    assert paths.blob(old_sha) == (default_root / "blobs" / old_sha[:2] / f"{old_sha}.pdf").resolve()
    assert paths.blob(new_sha) == (external / new_sha[:2] / f"{new_sha}.pdf")


def test_clearing_override_falls_back_to_default_root(tmp_path):
    paths = ManagedPaths(tmp_path / "data")
    paths.initialize()
    paths.set_blob_override(tmp_path / "ext")
    paths.set_blob_override(None)
    assert paths.blob_override() is None
    sha = _fake_pdf(paths, b"back-to-default")
    assert (tmp_path / "data" / "blobs" / sha[:2] / f"{sha}.pdf").is_file()


def test_blob_identity_is_validated(tmp_path):
    paths = ManagedPaths(tmp_path / "data")
    paths.initialize()
    with pytest.raises(PathContainmentError):
        paths.blob("../evil")


def test_migrate_status_and_run_move_only_missing_files(tmp_path):
    from reader_service.server import _library_migrate_run, _library_migrate_status

    default_root = tmp_path / "data"
    paths = ManagedPaths(default_root)
    paths.initialize()
    old_sha = _fake_pdf(paths, b"old-book")
    old_size = (default_root / "blobs" / old_sha[:2] / f"{old_sha}.pdf").stat().st_size

    external = tmp_path / "ext"
    paths.set_blob_override(external)

    # 还没搬：pending=1
    status = _library_migrate_status(paths)
    assert (status["pending"], status["bytes"]) == (1, old_size)

    # 先在外部放一个同名文件 → 迁移必须跳过、不覆盖
    same_name = external / old_sha[:2] / f"{old_sha}.pdf"
    same_name.parent.mkdir(parents=True)
    same_name.write_bytes(b"do-not-overwrite")

    result = _library_migrate_run(paths)
    assert result["moved"] == 0
    assert result["skipped"] == 1
    assert same_name.read_bytes() == b"do-not-overwrite"

    # 再在【默认】目录里放一个外部没有的文件（模拟换目录之前的旧书），迁移应搬它
    other_content = b"another-book"
    other_sha = hashlib.sha256(other_content).hexdigest()
    legacy = default_root / "blobs" / other_sha[:2] / f"{other_sha}.pdf"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(other_content)

    result = _library_migrate_run(paths)
    assert result["moved"] == 1
    assert (external / other_sha[:2] / f"{other_sha}.pdf").is_file()
    assert _library_migrate_status(paths)["pending"] == 0


def test_migrate_without_override_is_rejected(tmp_path):
    from reader_service.server import _library_migrate_run

    paths = ManagedPaths(tmp_path / "data")
    paths.initialize()
    with pytest.raises(ValueError):
        _library_migrate_run(paths)


def test_library_root_api_end_to_end(service, tmp_path):
    """换目录 → 新书落新目录 → 旧书仍可打开 → 迁移把旧文件搬过去。"""
    import json as jsonlib
    from urllib.request import Request, urlopen

    from test_api import make_pdf, request_json, running_server

    external = tmp_path / "外部书库"
    external.mkdir()

    with running_server(service) as (base, token):
        # 旧书：在默认位置导入
        old_pdf = make_pdf(((612, 792),))
        status, imported = request_json(
            f"{base}/api/books", token, method="POST", data=old_pdf,
            headers={"Content-Type": "application/pdf", "X-File-Name": "old.pdf"},
        )
        assert status == 201
        old_revision = imported["book"]["active_revision"]

        # 换目录
        status, stats = request_json(
            f"{base}/api/settings/library", token, method="PUT",
            data=jsonlib.dumps({"path": str(external)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert status == 200 and stats["is_default"] is False

        # 新书：落进新目录
        new_pdf = make_pdf(((612, 792), (792, 612)))
        status, imported2 = request_json(
            f"{base}/api/books", token, method="POST", data=new_pdf,
            headers={"Content-Type": "application/pdf", "X-File-Name": "new.pdf"},
        )
        assert status == 201
        new_revision = imported2["book"]["active_revision"]
        new_sha = new_revision["blob_sha256"]
        assert (external / new_sha[:2] / f"{new_sha}.pdf").is_file()

        # 旧书 PDF 仍可打开（默认位置读）
        request = Request(
            f"{base}/api/revisions/{old_revision['id']}/pdf",
            headers={"X-Reader-Token": token},
        )
        with urlopen(request) as response:
            assert response.status == 200
            assert response.read().startswith(b"%PDF")

        # 迁移：旧文件搬进新目录；迁移后旧书仍可读
        status, result = request_json(
            f"{base}/api/settings/library/migrate", token, method="POST",
            data=b"{}", headers={"Content-Type": "application/json"},
        )
        assert status == 200 and result["moved"] >= 1
        old_sha = old_revision["blob_sha256"]
        assert (external / old_sha[:2] / f"{old_sha}.pdf").is_file()
        request = Request(
            f"{base}/api/revisions/{old_revision['id']}/pdf",
            headers={"X-Reader-Token": token},
        )
        with urlopen(request) as response:
            assert response.status == 200

        # 拒绝系统目录与盘符根
        try:
            request_json(
                f"{base}/api/settings/library", token, method="PUT",
                data=jsonlib.dumps({"path": "c:\\windows\\Temp"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            raise AssertionError("系统目录应被拒绝")
        except Exception as error:
            assert getattr(error, "code", "") == 400 or "400" in str(error)
