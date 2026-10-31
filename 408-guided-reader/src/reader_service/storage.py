from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path, PurePath
from typing import BinaryIO


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PathContainmentError(ValueError):
    pass


class ManagedPaths:
    """The only boundary allowed to construct paths inside managed storage."""

    def __init__(self, root: Path, blob_override: Path | None = None):
        self.root = root.expanduser().resolve()
        # 2.0 §5.3：换书库目录 = 换"以后新书放哪"，不动任何已有文件。
        # override 只在 settings.json 配置了外部书库时存在；读永远两个根都找，
        # 所以换目录后旧书照常打开、新书落进新目录。
        self._blob_override: Path | None = None
        if blob_override is not None:
            self.set_blob_override(blob_override)

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # Windows packaged-app execution can virtualize LocalAppData only when the directory is
        # first created. Re-resolve after creation so containment compares canonical paths on both
        # sides of that redirection boundary.
        self.root = self.root.resolve()
        self.blob_root().mkdir(parents=True, exist_ok=True)
        self.temp_root().mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str | PurePath) -> Path:
        candidate = PurePath(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise PathContainmentError(f"Managed path escapes its root: {relative!s}")
        resolved = self.root.joinpath(*candidate.parts).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PathContainmentError(f"Managed path escapes its root: {relative!s}") from exc
        return resolved

    def database(self) -> Path:
        return self.resolve("state.sqlite3")

    def blob_root(self) -> Path:
        return self.resolve("blobs")

    def temp_root(self) -> Path:
        return self.resolve("tmp")

    # ---------- 外部书库（settings.json 的 library.root） ----------

    def set_blob_override(self, path: Path | None) -> None:
        if path is None:
            self._blob_override = None
            return
        resolved = Path(path).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        self._blob_override = resolved

    def blob_override(self) -> Path | None:
        return self._blob_override

    def _blob_name(self, sha256: str) -> PurePath:
        if not _SHA256.fullmatch(sha256):
            raise PathContainmentError("Blob identity must be a lowercase SHA-256 digest")
        return PurePath(sha256[:2], f"{sha256}.pdf")

    def blob(self, sha256: str) -> Path:
        """读路径：先找外部书库，再回退默认 blobs——换目录后旧书仍可打开。"""
        name = self._blob_name(sha256)
        if self._blob_override is not None:
            candidate = self._blob_override.joinpath(*name.parts)
            if candidate.is_file():
                return candidate
        return self.resolve(PurePath("blobs", *name.parts))

    def blob_target(self, sha256: str) -> Path:
        """写路径：新导入一律落进当前生效的书库目录。"""
        name = self._blob_name(sha256)
        if self._blob_override is not None:
            return self._blob_override.joinpath(*name.parts)
        return self.resolve(PurePath("blobs", *name.parts))

    def new_upload(self) -> tuple[Path, BinaryIO]:
        raw = tempfile.NamedTemporaryFile(
            mode="w+b", prefix="intake-", suffix=".pdf", dir=self.temp_root(), delete=False
        )
        return Path(raw.name), raw


class BlobStore:
    def __init__(self, paths: ManagedPaths):
        self.paths = paths

    def commit(self, temporary: Path, sha256: str) -> tuple[Path, bool]:
        destination = self.paths.blob_target(sha256)
        if destination.exists():
            temporary.unlink(missing_ok=True)
            return destination, False
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
        self._fsync_directory(destination.parent)
        return destination, True

    def delete(self, sha256: str) -> None:
        path = self.paths.blob(sha256)
        path.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
