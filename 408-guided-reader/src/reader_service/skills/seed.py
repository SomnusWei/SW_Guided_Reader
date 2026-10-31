"""随包技能种子：把 `<安装目录>/default-skills/` 落地到 `<data-dir>/skills/`。

三件事，边界都很窄：

1. `seed_default_skills()`——首启补种。目标已存在的技能**不覆盖**（用户可能手改过
   SKILL.md），只补缺失文件；返回装了什么、补了什么，便于引导页显示。
2. `restore_skill()`——「恢复内置知识包」按钮的后端。整套覆盖，用于修被删坏的包。
3. `list_seed_skills()`——设置页要显示「内置 · 5.4MB」这类信息时用。

设计约束（对应方案 §6.3 / §6.4）：
  * 种子目录缺失时**降级不报错**：Reader 照常启动，AI 退回通用解释。
  * 复制时跳过 `__pycache__`/`.git`，并**跳过资料层大目录**（`中医教材/` 等
    1.37GB）——种子只带认知层，用户误把完整目录设为种子源时也不会复制出 1.3GB。
  * 技能名沿用 SKILL.md frontmatter 的 `name`（现有 `SkillRegistry` 也是用它），
    与目录名不一致时以 frontmatter 为准，避免装出"目录名 ≠ 技能名"的错位。
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from reader_service.appbase import candidate_bases
from reader_service.skills.registry import SkillRecord, parse_frontmatter

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_ENV = "GUIDED_READER_DEFAULT_SKILLS_DIR"
# 兼容 1.x 的单技能环境变量
LEGACY_SKILL_ENV = "GUIDED_READER_DEFAULT_SKILL_DIR"

# 复制时永远跳过的名字（任意层级）
SKIP_NAMES = {"__pycache__", ".git", ".idea", ".vscode", ".DS_Store", "Thumbs.db"}
# 资料层：种子不随包，即便源目录里有也不复制（方案 §6.3）
SKIP_DATA_DIRS = {"中医教材", "西医教材", "石学敏体系", "倪海厦体系", "蒸馏产出", "platform"}


@dataclass
class SeedResult:
    installed: list[str] = field(default_factory=list)   # 新建落地
    filled: list[str] = field(default_factory=list)      # 已存在，补齐了缺件
    skipped: list[str] = field(default_factory=list)     # 已存在且完整
    unavailable: bool = False                            # 种子源不可用（降级）

    def to_json(self) -> dict:
        return {
            "installed": list(self.installed),
            "filled": list(self.filled),
            "skipped": list(self.skipped),
            "unavailable": self.unavailable,
        }


def _skill_name(directory: Path) -> str | None:
    """读 SKILL.md 取技能名；没有 SKILL.md 或 frontmatter 无效则返回 None。"""
    skill_md = directory / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        parsed = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.warning("读取 SKILL.md 失败：%s（%s）", skill_md, exc)
        return None
    if parsed is None:
        return None
    meta, body = parsed
    if not body:
        return None
    name = str(meta.get("name") or "").strip() or directory.name
    if not name or name in {".", ".."} or any(ch in name for ch in '\\/:*?"<>|'):
        logger.warning("技能名无效，跳过：%r", name)
        return None
    return name


def _iter_sources(root: Path) -> list[tuple[str, Path]]:
    """种子源可以是「一个技能目录」也可以是「装着多个技能目录的根」。"""
    if not root.is_dir():
        return []
    if (root / "SKILL.md").is_file():
        name = _skill_name(root)
        return [(name, root)] if name else []
    found: list[tuple[str, Path]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in SKIP_NAMES or entry.name.startswith("."):
            continue
        name = _skill_name(entry)
        if name:
            found.append((name, entry))
    return found


def resolve_seed_root(source: str | os.PathLike | None = None) -> Path | None:
    """定位种子源目录：显式参数 → 环境变量 → 安装目录 → 仓库根。"""
    for candidate in (source, os.environ.get(DEFAULT_SKILLS_ENV), os.environ.get(LEGACY_SKILL_ENV)):
        if candidate:
            path = Path(candidate)
            return path if path.is_dir() else None
    # 打包后：<安装目录>/reader-service/_internal/default-skills（PyInstaller onedir，
    # 走 appbase 候选基目录）；开发时：仓库根/default-skills（兼容旧 parents[3]/[4]）。
    bases: list[Path] = list(candidate_bases())
    here = Path(__file__).resolve()
    bases.extend(here.parents[3:5])
    for base in bases:
        if base is None:
            continue
        candidate = base / "default-skills"
        if candidate.is_dir():
            return candidate
    return None


def _copy_missing(src: Path, dst: Path) -> int:
    """把 src 里 dst 缺失的文件补进去，返回补了几个。不覆盖已有文件。"""
    filled = 0
    for item in src.iterdir():
        if item.name in SKIP_NAMES or item.name in SKIP_DATA_DIRS:
            continue
        target = dst / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            filled += _copy_missing(item, target)
        elif item.is_file() and not target.exists():
            shutil.copy2(item, target)
            filled += 1
    return filled


def _copy_full(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP_NAMES or item.name in SKIP_DATA_DIRS:
            continue
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(*SKIP_NAMES))
        else:
            shutil.copy2(item, target)


def seed_default_skills(
    data_dir: Path, source: str | os.PathLike | None = None
) -> SeedResult:
    """首启补种：把种子技能落地到 `<data-dir>/skills/`，幂等且不覆盖用户改动。"""
    result = SeedResult()
    root = resolve_seed_root(source)
    if root is None:
        result.unavailable = True
        return result
    skills_dir = Path(data_dir) / "skills"
    sources = _iter_sources(root)
    if not sources:
        result.unavailable = True
        return result
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name, src in sources:
        target = skills_dir / name
        try:
            if not target.exists():
                _copy_full(src, target)
                result.installed.append(name)
            else:
                if _copy_missing(src, target):
                    result.filled.append(name)
                else:
                    result.skipped.append(name)
        except OSError as exc:
            logger.warning("技能种子补种失败：%s（%s）", name, exc)
            result.unavailable = True
    return result


TRASH_NAME = "skills-trash"


def restore_skill(
    data_dir: Path, name: str, source: str | os.PathLike | None = None
) -> SkillRecord:
    """用包内版本整体覆盖一个种子技能（「恢复内置知识包」）。

    实现刻意"先建新的、再把旧的挪走"，不做 `rmtree(旧目录)`：
      * 覆盖过程中任何一步失败，用户的旧技能都还在（最坏是多一个暂存目录）；
      * 旧的被挪到 `<data-dir>/skills-trash/<时间戳>-<name>/` 而不是删掉，
        用户改过的东西还能捞回来。
    """
    root = resolve_seed_root(source)
    if root is None:
        raise LookupError("找不到内置知识包目录（default-skills）。")
    match = next((src for skill, src in _iter_sources(root) if skill == name), None)
    if match is None:
        raise LookupError(f"内置知识包里没有这个技能：{name}")
    skills_dir = Path(data_dir) / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    target = skills_dir / name
    staging = skills_dir / f".{name}.restoring"
    if staging.exists():
        shutil.rmtree(staging)
    _copy_full(match, staging)
    if target.exists():
        trash_root = Path(data_dir) / TRASH_NAME
        trash_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        trashed = trash_root / f"{stamp}-{name}"
        suffix = 1
        while trashed.exists():
            suffix += 1
            trashed = trash_root / f"{stamp}-{name}-{suffix}"
        shutil.move(str(target), str(trashed))
    staging.replace(target)
    skill_md = target / "SKILL.md"
    parsed = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    meta, body = parsed if parsed else ({}, "")
    return SkillRecord(
        name=name,
        display_name=str(meta.get("name") or name),
        description=str(meta.get("description") or ""),
        body=body,
        references_dir=(target / "references") if (target / "references").is_dir() else None,
        scripts_dir=(target / "scripts") if (target / "scripts").is_dir() else None,
        is_default=True,
    )


def seed_skill_names(source: str | os.PathLike | None = None) -> list[str]:
    """种子源里有哪些技能名（设置页展示用；源不可用时返回空）。"""
    root = resolve_seed_root(source)
    return [name for name, _ in _iter_sources(root)] if root else []


def is_seeded_skill(data_dir: Path, name: str, source: str | os.PathLike | None = None) -> bool:
    """`<data-dir>/skills/<name>` 是否来自随包种子。"""
    if name not in seed_skill_names(source):
        return False
    return (Path(data_dir) / "skills" / name).is_dir()


def directory_size(path: Path) -> int:
    """目录总字节数（跳过 `__pycache__`，避免把编译缓存算成"知识包体积"）。"""
    total = 0
    root = Path(path)
    if not root.is_dir():
        return 0
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_NAMES]
        for name in files:
            if name in SKIP_NAMES:
                continue
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"
