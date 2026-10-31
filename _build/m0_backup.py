#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""M0 备份：源码包 + 数据冷备 + 安装包归档 + 恢复脚本，并输出 MANIFEST。

用法：
    python _build/m0_backup.py              # 备份到 E:\backup\LearningTools-2.0-M0-<时间戳>
    python _build/m0_backup.py --dest D:\x  # 指定目录
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # learningtools-main
READER_DIR = ROOT / "408-guided-reader"
DATA_DIR = ROOT / "Reader"
INSTALLER = ROOT / "成品" / "安装版" / "LearningTools-1.3.1-安装包.exe"
DEFAULT_DEST_ROOT = Path(r"E:\backup")

# 源码包排除项（相对 ROOT 的路径前缀）
SRC_EXCLUDES = (
    "app/node_modules", "app/target", "app/dist",
    "408-guided-reader/.venv", "408-guided-reader/.pytest_cache",
    "408-guided-reader/__pycache__", "Reader", "成品", "test-results",
    "_backup", ".git",
)
# 任意层级都要排除的目录名
SRC_EXCLUDE_NAMES = {"node_modules", "__pycache__", ".pytest_cache", ".venv", "target", ".mypy_cache"}


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def git(*args: str, cwd: Path = READER_DIR) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception as exc:  # noqa: BLE001
        return f"<git failed: {exc}>"


def should_skip(rel: str) -> bool:
    rel_posix = rel.replace("\\", "/")
    for prefix in SRC_EXCLUDES:
        if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
            return True
    return any(part in SRC_EXCLUDE_NAMES for part in rel_posix.split("/"))


def backup_source(dest: Path) -> dict:
    """源码整包（tar.gz），排除依赖与构建缓存。"""
    archive = dest / "src" / "learningtools-main-src.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
        for path in sorted(ROOT.rglob("*")):
            rel = str(path.relative_to(ROOT))
            if should_skip(rel):
                continue
            if path.is_symlink():
                continue
            tar.add(str(path), arcname=rel, recursive=False)
            if path.is_file():
                count += 1
    return {
        "archive": str(archive),
        "files": count,
        "bytes": archive.stat().st_size,
        "sha256": sha256(archive),
    }


def backup_sqlite(src: Path, dest: Path) -> dict:
    """用 sqlite3 在线备份 API，含 WAL 内容、保证一致性。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    conn = sqlite3.connect(str(dest))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "sha256": sha256(dest),
        "integrity": integrity,
        "tables": tables,
    }


def backup_blobs(src: Path, dest: Path) -> dict:
    if not src.is_dir():
        return {"copied": 0, "bytes": 0, "skipped": True}
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    files = sum(1 for p in dest.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    return {"path": str(dest), "copied": files, "bytes": size}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=None)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(args.dest) if args.dest else DEFAULT_DEST_ROOT / f"LearningTools-2.0-M0-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] 备份目录：{dest}")
    manifest: dict = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(ROOT),
        "dest": str(dest),
        "git": {
            "tag": git("describe", "--tags", "--exact-match", "HEAD"),
            "commit": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        },
    }

    print("[2/5] 源码整包（排除 node_modules / target / .venv / Reader / 成品）…")
    manifest["source"] = backup_source(dest)
    print(f"       {manifest['source']['files']} 个文件 → {manifest['source']['bytes']/1048576:.1f} MB")

    print("[3/5] 数据冷备（state.sqlite3 在线备份 + blobs 复制）…")
    data_dest = dest / "data"
    state_src = DATA_DIR / "state.sqlite3"
    if state_src.exists():
        manifest["state"] = backup_sqlite(state_src, data_dest / "state.sqlite3")
        print(f"       state.sqlite3 {manifest['state']['bytes']/1048576:.1f} MB，"
              f"integrity={manifest['state']['integrity']}，表 {manifest['state']['tables']} 个")
    else:
        manifest["state"] = {"missing": True}
        print("       ⚠ 未找到 state.sqlite3")

    manifest["blobs"] = backup_blobs(DATA_DIR / "blobs", data_dest / "blobs")
    if not manifest["blobs"].get("skipped"):
        print(f"       blobs {manifest['blobs']['copied']} 个文件 "
              f"{manifest['blobs']['bytes']/1048576:.1f} MB")

    print("[4/5] 归档 1.3.1 安装包 + 恢复脚本…")
    if INSTALLER.exists():
        target = dest / "installer" / INSTALLER.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INSTALLER, target)
        manifest["installer"] = {"path": str(target), "bytes": target.stat().st_size,
                                 "sha256": sha256(target)}
        print(f"       {INSTALLER.name} {target.stat().st_size/1048576:.1f} MB")
    else:
        manifest["installer"] = {"missing": True}
        print("       ⚠ 未找到 1.3.1 安装包")

    restore_src = ROOT / "restore-1.3.1.ps1"
    if restore_src.exists():
        shutil.copy2(restore_src, dest / "restore-1.3.1.ps1")
        manifest["restore_script"] = "restore-1.3.1.ps1"

    print("[5/5] 写 MANIFEST.json…")
    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    print(f"\n完成：{dest}")
    print(f"总计 {total/1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
