#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yixue-zonghe 技能依赖自检。

逐项导入技能所需第三方库，报告可用性与版本；
并实际调用 text_utils 的核心函数，确认端到端可用。

用法：
  python check_deps.py
"""

import io
import sys
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# (显示名, import 名, pip 包名, 用途, 是否必需)
DEPS = [
    ("PyMuPDF",      "pymupdf",            "pymupdf",     "PDF 文本层提取",        True),
    ("Pillow",       "PIL",                "pillow",      "图片解码（OCR 前置）",   False),
    ("NumPy",        "numpy",              "numpy",       "图像数组（OCR 前置）",   False),
    ("RapidOCR",     "rapidocr",           "rapidocr",    "扫描页/图片 OCR",       False),
    ("python-docx",  "docx",               "python-docx", "DOCX 教材读取",        False),
    ("pywin32",      "win32com.client",    "pywin32",     "DOC 转换（需装 Word）", False),
]


def check_one(display, modname, pkgname, purpose, required):
    try:
        mod = __import__(modname, fromlist=["__version__"])
        ver = getattr(mod, "__version__", None)
        if ver is None:
            # 部分包版本在子模块或 metadata
            try:
                from importlib.metadata import version as _v
                ver = _v(pkgname)
            except Exception:
                ver = "?"
        return True, f"✅ {display:<12} {str(ver):<12} {purpose}"
    except Exception as e:
        flag = "❌" if required else "⚠️ "
        tail = "（必需）" if required else "（可选，缺失则降级）"
        return not required, f"{flag} {display:<12} {'缺失'.ljust(12)} {purpose} {tail}  [{type(e).__name__}]"


def main():
    print("=" * 70)
    print("yixue-zonghe 依赖自检")
    print("=" * 70)
    print(f"Python: {sys.version.split()[0]}")
    print(f"解释器: {sys.executable}")
    print()

    all_ok = True
    for d, m, p, u, r in DEPS:
        ok, line = check_one(d, m, p, u, r)
        print(line)
        if not ok:
            all_ok = False

    print()
    print("-" * 70)
    print("功能探测")
    print("-" * 70)

    # 1) text_utils 核心读取（纯标准库，必须通过）
    skill_scripts = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    sys.path.insert(0, skill_scripts)
    try:
        from text_utils import detect_encoding, read_text, extract_file_text  # noqa
        tb = os.path.abspath(os.path.join(skill_scripts, "..", "中医教材"))
        txts = [f for f in os.listdir(tb) if f.lower().endswith(".txt")] if os.path.isdir(tb) else []
        print(f"✅ 编码读取模块就绪；中医教材目录发现 {len(txts)} 个 txt")
        if txts:
            sample = os.path.join(tb, txts[0])
            print(f"   抽样: {txts[0]} -> 编码={detect_encoding(sample)}")
    except Exception as e:
        print(f"❌ text_utils 导入失败: {e!r}")
        all_ok = False

    # 2) PDF 能力
    try:
        from text_utils import extract_pdf_text  # noqa
        import pymupdf  # noqa
        print("✅ PDF 提取链路可用（PyMuPDF）")
    except Exception as e:
        print(f"❌ PDF 提取不可用: {e!r}")
        all_ok = False

    # 3) OCR 能力
    try:
        from rapidocr import RapidOCR
        RapidOCR()
        print("✅ OCR 引擎可初始化（RapidOCR）")
    except Exception as e:
        print(f"⚠️  OCR 不可用（扫描版 PDF 将只有文本层结果）: {type(e).__name__}: {e}")

    # 4) DOCX 能力
    try:
        import docx  # noqa
        print("✅ DOCX 读取可用（python-docx）")
    except Exception as e:
        print(f"⚠️  DOCX 读取不可用: {type(e).__name__}")

    print()
    print("=" * 70)
    print("结论: " + ("核心依赖齐全 ✅" if all_ok else "存在必需依赖缺失 ❌"))
    print("=" * 70)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
