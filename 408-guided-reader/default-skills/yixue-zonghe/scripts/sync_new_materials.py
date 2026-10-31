#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
医学知识库增量同步脚本（多格式导入）

功能：扫描教材目录中新增的 PDF/DOCX/DOC/TXT 文件，自动提取文本
      （PDF：文本层 + 扫描页 OCR + 内嵌图片 OCR；DOCX：段落+表格；
        DOC：Word COM 转换；TXT：编码转换），生成同名 .txt 文件并重建索引。

用法：
  python sync_new_materials.py --track 中医              # 处理中医教材目录所有新增文件
  python sync_new_materials.py --track 西医              # 处理西医教材目录
  python sync_new_materials.py --rebuild                  # 强制重建所有文件对应的 txt
  python sync_new_materials.py --only "中医外科学"         # 只处理文件名含关键字的文件
  python sync_new_materials.py --no-ocr                   # 不做 OCR（扫描页/图片将为空）
  python sync_new_materials.py --no-images-ocr            # 不提取 PDF 内嵌图片 OCR（仅整页 OCR）
  python sync_new_materials.py --no-index                 # 处理后不重建索引
  python sync_new_materials.py --from "D:/待导入"          # 从指定目录导入文件到教材目录后处理
"""

import os
import sys
import io
import shutil
import argparse
import traceback
from datetime import datetime

# 统一 UTF-8 输出，避免 Windows GBK 控制台崩溃
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from text_utils import extract_file_text, SUPPORTED_EXTENSIONS  # noqa: E402

# 支持的源文件扩展名
SOURCE_EXTS = {".pdf", ".docx", ".doc", ".txt"}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def find_sources(textbook_dir, only_keyword=None):
    """返回教材目录下所有支持格式文件的绝对路径列表（按文件名排序）。"""
    sources = []
    for fname in sorted(os.listdir(textbook_dir)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in SOURCE_EXTS:
            continue
        # 跳过已经生成的 .txt（只处理源文件）
        if ext == ".txt":
            # 检查是否有同名源文件（pdf/docx/doc），有则跳过此 txt
            stem = os.path.splitext(fname)[0]
            has_source = any(
                os.path.exists(os.path.join(textbook_dir, stem + e))
                for e in (".pdf", ".docx", ".doc")
            )
            if has_source:
                continue
        if only_keyword and only_keyword not in fname:
            continue
        sources.append(os.path.join(textbook_dir, fname))
    return sources


def needs_sync(src_path, txt_path):
    """判断源文件是否需要（重新）生成 txt。"""
    if not os.path.exists(txt_path):
        return True
    # 源文件比 txt 新 → 重新生成
    return os.path.getmtime(src_path) > os.path.getmtime(txt_path)


def sync_one(src_path, use_ocr=True, ocr_engine=None, dpi=200, extract_images=True):
    """处理单个源文件：提取文本 → 写入同名 .txt。返回 (txt_path, stats) 或 (None, None)。"""
    txt_path = os.path.splitext(src_path)[0] + ".txt"
    src_name = os.path.basename(src_path)
    ext = os.path.splitext(src_path)[1].lower()
    log(f"处理: {src_name}")
    try:
        kwargs = {"dpi": dpi, "use_ocr": use_ocr, "ocr_engine": ocr_engine,
                  "extract_images": extract_images} if ext == ".pdf" else {}
        text, stats = extract_file_text(src_path, **kwargs)

        if not text.strip():
            log(f"  警告: 提取结果为空，跳过写入 {src_name}")
            return None, stats

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        # 格式化 stats 摘要
        if ext == ".pdf":
            detail = (f"{stats.get('pages', '?')}页 "
                      f"(文本层{stats.get('text_pages', 0)}页 / "
                      f"整页OCR{stats.get('ocr_pages', 0)}页 / "
                      f"图片OCR{stats.get('images_ocr', 0)}张)")
        elif ext in (".docx", ".doc"):
            detail = f"段落{stats.get('paragraphs', '?')} / 表格{stats.get('tables', '?')}"
        else:
            detail = stats.get("encoding", "")

        log(f"  完成: {detail} 来源={stats.get('source', ext)} "
            f"-> {os.path.basename(txt_path)} ({len(text)} chars)")
        return txt_path, stats
    except Exception as e:
        log(f"  失败: {e!r}")
        traceback.print_exc()
        return None, None


def rebuild_index(textbook_dir, index_path, track):
    """调用 build_textbook_index.py 重建索引。"""
    import subprocess
    build_script = os.path.join(SCRIPT_DIR, "build_textbook_index.py")
    log(f"重建索引: {index_path}")
    cmd = [sys.executable, build_script, "--textbook-dir", textbook_dir,
           "--output", index_path, "--track", track]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.stdout:
        for line in result.stdout.splitlines():
            log(f"  [build] {line}")
    if result.returncode != 0:
        log(f"  索引构建失败 (exit={result.returncode}):")
        if result.stderr:
            for line in result.stderr.splitlines():
                log(f"  [build-err] {line}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="医学知识库多格式导入与增量同步")
    parser.add_argument("--textbook-dir", default=None,
        help="教材目录路径（不指定则按 --track 自动选择）")
    parser.add_argument("--track", choices=["中医", "西医"], default="中医",
        help="医学体系：中医/西医（默认中医）")
    parser.add_argument("--index", default=None,
        help="索引文件输出路径（不指定则按 track 自动选择）")
    parser.add_argument("--from", dest="from_dir", default=None,
        help="从指定目录导入文件到教材目录后处理（复制所有支持格式文件）")
    parser.add_argument("--rebuild", action="store_true",
                        help="强制重建所有文件对应的 txt（忽略已存在的）")
    parser.add_argument("--only", default=None,
                        help="只处理文件名包含此关键字的文件")
    parser.add_argument("--no-ocr", action="store_true",
                        help="不做 OCR（PDF 扫描页和图片将为空）")
    parser.add_argument("--no-images-ocr", action="store_true",
                        help="不提取 PDF 内嵌图片 OCR（仅对扫描页整页 OCR）")
    parser.add_argument("--no-index", action="store_true",
                        help="处理完成后不重建索引")
    parser.add_argument("--dpi", type=int, default=200,
                        help="OCR 渲染 DPI（默认 200，越高越清晰但越慢）")
    args = parser.parse_args()

    if args.textbook_dir is None:
        sub = "中医教材" if args.track == "中医" else "西医教材"
        args.textbook_dir = os.path.abspath(os.path.join(SCRIPT_DIR, "..", sub))

    if args.index is None:
        ref_dir = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "references"))
        if args.track == "中医":
            args.index = os.path.join(ref_dir, "textbook-index.md")
        else:
            args.index = os.path.join(ref_dir, "textbook-index-西医.md")

    if not os.path.isdir(args.textbook_dir):
        log(f"错误: 教材目录不存在: {args.textbook_dir}")
        sys.exit(1)

    # 从指定目录导入文件
    if args.from_dir:
        if not os.path.isdir(args.from_dir):
            log(f"错误: 导入目录不存在: {args.from_dir}")
            sys.exit(1)
        imported = 0
        for fname in os.listdir(args.from_dir):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SOURCE_EXTS:
                continue
            src = os.path.join(args.from_dir, fname)
            dst = os.path.join(args.textbook_dir, fname)
            if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
                log(f"导入: {fname}")
                imported += 1
        log(f"从 {args.from_dir} 导入 {imported} 个文件")

    sources = find_sources(args.textbook_dir, args.only)
    if not sources:
        log("未找到任何可处理的源文件（pdf/docx/doc/txt）。")
        return

    log(f"共发现 {len(sources)} 个源文件")

    # 筛选需要同步的
    to_process = []
    for src in sources:
        txt = os.path.splitext(src)[0] + ".txt"
        if args.rebuild or needs_sync(src, txt):
            to_process.append(src)

    if not to_process:
        log("所有源文件均已同步，无需处理。")
        return

    log(f"需要处理 {len(to_process)} 个文件")

    # 预初始化 OCR 引擎
    ocr_engine = None
    if not args.no_ocr:
        log("初始化 OCR 引擎...")
        try:
            from rapidocr import RapidOCR
            ocr_engine = RapidOCR()
            log("OCR 引擎就绪")
        except Exception as e:
            log(f"警告: OCR 引擎初始化失败 ({e!r})，将仅提取文本层")

    success = 0
    failed = 0
    for src in to_process:
        txt_path, stats = sync_one(
            src, use_ocr=not args.no_ocr, ocr_engine=ocr_engine,
            dpi=args.dpi, extract_images=not args.no_images_ocr,
        )
        if txt_path:
            success += 1
        else:
            failed += 1

    log(f"同步完成: 成功 {success}，失败 {failed}")

    if not args.no_index:
        ok = rebuild_index(args.textbook_dir, args.index, args.track)
        if ok:
            log(f"索引已更新: {args.index}")
        else:
            log("索引更新失败，请手动运行 build_textbook_index.py")


if __name__ == "__main__":
    main()
