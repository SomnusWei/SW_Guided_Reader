# -*- mode: python ; coding: utf-8 -*-
"""reader-service sidecar 打包配置（PyInstaller --onedir）

产物：reader-service/reader-service.exe + _internal/
由 Tauri 作为资源打包进最终应用，由 Rust 负责拉起与退出清理。
"""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

SIDE_DIR = os.path.dirname(os.path.abspath(SPEC))
REPO = os.path.dirname(SIDE_DIR)
SRC = os.path.join(REPO, "src")

datas = collect_data_files("reader_service")
# 防御：collect_data_files 依赖 .venv 的 editable 安装（.pth 指向 src）。
# 目录改名/换机后 .pth 失效时它会静默跳过，产出缺 static 的坏包——这里
# 直接失败并提示修复方向，绝不带病打包。
if not any("static" in str(dest) for _, dest in datas):
    raise SystemExit(
        "collect_data_files('reader_service') 没有收集到 static 资源：\n"
        "  .venv 的 editable 安装（__editable__*.pth）可能指向了不存在的旧路径，\n"
        "  请把 .pth 内容改为当前 408-guided-reader/src 的绝对路径后重试。"
    )

# 2.0 随包资源（M6）：
#   default-skills/  技能种子（认知层，~5MB；首启补种到数据目录）
#   plugins/         随包插件（中医辞典 161MB 数据 + 拼音标注）
# 它们必须落在 reader-service/ 目录内：seed.py 与 plugins/registry.py 都按
# `Path(__file__).parents[3]`（即 reader-service/）找这两个目录。
def _include_tree(name):
    source = os.path.join(REPO, name)
    if not os.path.isdir(source):
        raise SystemExit(f"随包资源缺失：{source}（先跑 _build/m4_plugins.py / m3_seed_skill.py）")
    for base, _, files in os.walk(source):
        for file in files:
            full = os.path.join(base, file)
            rel = os.path.relpath(full, REPO)
            datas.append((full, os.path.dirname(rel)))


_include_tree("default-skills")
_include_tree("plugins")

binaries = collect_dynamic_libs("pypdfium2")

a = Analysis(
    [os.path.join(SIDE_DIR, "entry.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "pypdf",
        "pypdfium2",
        "numpy",
        "reader_service",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "setuptools", "pip", "wheel"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="reader-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="reader-service",
)
