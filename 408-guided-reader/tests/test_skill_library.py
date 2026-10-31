"""资料库挂载（§6.5）：只登记路径，校验严格，移除只删登记不动文件。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from reader_service.skills import LibraryMount, LibraryMountError, validate_mount_path

SKILL_MD = "---\nname: yixue-zonghe\ndescription: 中医学习包\n---\n\n正文\n"


def make_skill(root: Path, name: str = "yixue-zonghe", *, textbooks: int = 0) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    if textbooks:
        books = directory / "中医教材"
        books.mkdir()
        for index in range(textbooks):
            (books / f"book-{index}.txt").write_text("教材", encoding="utf-8")
    return directory


@pytest.fixture
def mounts(tmp_path):
    return LibraryMount(tmp_path / "data")


def test_unmounted_status(mounts):
    status = mounts.status("yixue-zonghe")
    assert status == {
        "mounted": False, "path": None, "reachable": False,
        "textbooks": [], "textbook_dirs": [],
    }


def test_mount_records_path_without_copying(mounts, tmp_path):
    skill = make_skill(tmp_path / "src", textbooks=3)
    status = mounts.mount("yixue-zonghe", str(skill))
    assert status["mounted"] is True
    assert status["reachable"] is True
    assert status["textbooks"] == [{"name": "中医教材", "files": 3}]
    payload = json.loads((mounts.data_dir / "skills" / "library.json").read_text(encoding="utf-8"))
    assert payload["yixue-zonghe"]["path"] == str(skill.resolve())
    # §13.13 #1：登记时就记下资料层目录名，2.1 不必再猜
    assert payload["yixue-zonghe"]["textbook_dirs"] == ["中医教材"]
    # §13.13 #1：登记时就记下资料层目录名，2.1 不必再猜
    assert payload["yixue-zonghe"]["textbook_dirs"] == ["中医教材"]
    # 只登记：数据目录里没有多出教材文件
    assert not list((mounts.data_dir / "skills").rglob("*.txt"))


def test_mount_is_persisted_and_reloadable(mounts, tmp_path):
    skill = make_skill(tmp_path / "src")
    mounts.mount("yixue-zonghe", str(skill))
    fresh = LibraryMount(mounts.data_dir)
    assert fresh.status("yixue-zonghe")["mounted"] is True


def test_mount_rejects_relative_path(mounts, tmp_path):
    make_skill(tmp_path / "src")
    with pytest.raises(LibraryMountError):
        mounts.mount("yixue-zonghe", "src/yixue-zonghe")


def test_mount_rejects_missing_path(mounts, tmp_path):
    with pytest.raises(LibraryMountError):
        mounts.mount("yixue-zonghe", str(tmp_path / "nowhere"))


def test_mount_rejects_directory_without_skill_md(mounts, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(LibraryMountError):
        mounts.mount("yixue-zonghe", str(plain))


def test_mount_rejects_drive_root(mounts):
    root = Path(os.path.abspath(os.sep))
    with pytest.raises(LibraryMountError):
        validate_mount_path(str(root))


def test_mount_rejects_system_directory(mounts):
    with pytest.raises(LibraryMountError):
        validate_mount_path(r"C:\Windows")


def test_unreachable_path_is_reported_not_raised(mounts, tmp_path):
    skill = make_skill(tmp_path / "src")
    mounts.mount("yixue-zonghe", str(skill))
    import shutil

    shutil.rmtree(skill)
    status = mounts.status("yixue-zonghe")
    assert status["mounted"] is True
    assert status["reachable"] is False
    assert status["textbooks"] == []
    assert "不可达" in status["message"]


def test_unmount_only_removes_registration(mounts, tmp_path):
    skill = make_skill(tmp_path / "src", textbooks=1)
    mounts.mount("yixue-zonghe", str(skill))
    status = mounts.unmount("yixue-zonghe")
    assert status["mounted"] is False
    assert (skill / "SKILL.md").is_file()


def test_unmount_unknown_is_noop(mounts):
    assert mounts.unmount("nope")["mounted"] is False


def test_corrupt_library_json_is_tolerated(mounts):
    path = mounts.data_dir / "skills" / "library.json"
    path.parent.mkdir(parents=True)
    path.write_text("不是 JSON", encoding="utf-8")
    assert mounts.load() == {}
    assert mounts.status("yixue-zonghe")["mounted"] is False
