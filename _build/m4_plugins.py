# -*- coding: utf-8 -*-
"""M4：把两个工具搬成随包插件（可重复执行）。

做三件事：
  1. `tools/dict/data/`（161MB，不进 git）→ `plugins/dict/data/`，只在缺失或数量不同步时才复制；
  2. 重新生成 `plugins/dict/index.html`（调 m4_dict_plugin.py，保证与源页面同步）；
  3. `tools/pinyin-pro.js` → `plugins/pinyin/pinyin-pro.js`（按字节比较，不同才覆盖）。

`plugins/*/index.html`、`plugin.json`、`icon.svg` 是手写并入库的，本脚本不生成它们
（除了 dict 的 index.html——它必须由源页面派生，手改会丢同步）。

用法：python _build/m4_plugins.py [--force]
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
READER = REPO / "408-guided-reader"

DICT_SRC = REPO / "tools" / "dict" / "data"
DICT_DST = READER / "plugins" / "dict" / "data"
PINYIN_SRC = REPO / "tools" / "pinyin-pro.js"
PINYIN_DST = READER / "plugins" / "pinyin" / "pinyin-pro.js"


def sync_dict_data(force: bool) -> None:
    if not DICT_SRC.is_dir():
        raise SystemExit(f"找不到词典数据源：{DICT_SRC}")
    src_files = sorted(p for p in DICT_SRC.rglob("*") if p.is_file())
    dst_files = sorted(p for p in DICT_DST.rglob("*") if p.is_file()) if DICT_DST.is_dir() else []
    if not force and len(src_files) == len(dst_files) and DICT_DST.is_dir():
        print(f"词典数据已同步（{len(src_files)} 个文件），跳过")
        return
    if DICT_DST.exists():
        shutil.rmtree(DICT_DST)
    shutil.copytree(DICT_SRC, DICT_DST)
    total = sum(p.stat().st_size for p in DICT_DST.rglob("*") if p.is_file())
    print(f"词典数据已复制：{len(src_files)} 个文件 / {total / 1024 / 1024:.1f}MB")


def rebuild_dict_page() -> None:
    script = HERE / "m4_dict_plugin.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    print(result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        raise SystemExit("词典插件页生成失败")


def sync_pinyin_lib() -> None:
    if not PINYIN_SRC.is_file():
        raise SystemExit(f"找不到 pinyin-pro.js：{PINYIN_SRC}")
    if PINYIN_DST.is_file() and filecmp.cmp(PINYIN_SRC, PINYIN_DST, shallow=False):
        print("pinyin-pro.js 已同步，跳过")
        return
    shutil.copy2(PINYIN_SRC, PINYIN_DST)
    print(f"pinyin-pro.js 已复制：{PINYIN_DST.stat().st_size / 1024:.0f}KB")


def main() -> int:
    force = "--force" in sys.argv
    sync_dict_data(force)
    rebuild_dict_page()
    sync_pinyin_lib()
    for plugin_id in ("dict", "pinyin"):
        root = READER / "plugins" / plugin_id
        missing = [name for name in ("plugin.json", "index.html", "icon.svg") if not (root / name).is_file()]
        if missing:
            raise SystemExit(f"{plugin_id} 缺文件：{missing}")
    print("两个插件就绪：plugins/dict、plugins/pinyin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
