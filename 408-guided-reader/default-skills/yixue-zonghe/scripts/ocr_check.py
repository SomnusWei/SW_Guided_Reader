# -*- coding: utf-8 -*-
"""OCR 错字核验工具（中医内科学讲义专用）

用法：
    python ocr_check.py <待查文本文件.txt 或 .html>
    python ocr_check.py --text "要检查的文本"

检查范围：
1. 形近字词典扫描（㿠/恍、脘/皖、溲/搜、颧/颧、咯/咯、悸/季、窒/室…）
2. 音近字词典扫描（痰/谈、湿/温、瘀/淤、濡/儒、弦/玄…）
3. 中医术语核对：方名、药名、脉象名、舌象名常见 OCR 误写
4. 漏字/串行提示（连续重复标点、异常空格）

输出：逐项列出「位置 | 疑似误字 | 正确候选 | 上下文」
仅报告可疑项，不自动改写（保留原字，由人工/回搜教材确认）。
"""
import re
import sys
import argparse
from pathlib import Path

# ===== 形近字：OCR 常见误识（上下文模式匹配，降低误报）=====
# 每项: (误写模式 regex, 正确字, 说明)
# 仅收录在特定上下文中高度可疑的形近误识，避免"四季""阳光""恍惚"等误报
XINGJIN_PATTERNS = [
    (r"面色恍白", "面色㿠白", "恍→㿠"),
    (r"面色光白", "面色㿠白", "光→㿠"),
    (r"皖腹", "脘腹", "皖→脘"),
    (r"胃脘", "胃脘", "腕→脘（脏腑语境）"),
    (r"小便搜黄", "小便溲黄", "搜→溲"),
    (r"搜黄", "溲黄", "搜→溲"),
    (r"脉玄", "脉弦", "玄→弦"),
    (r"脉象玄", "脉象弦", "玄→弦"),
    (r"脉儒", "脉濡", "儒→濡"),
    (r"脉象儒", "脉象濡", "儒→濡"),
    (r"心季", "心悸", "季→悸"),
    (r"悸动不安", "悸动不安", "保留"),
    (r"结伐", "结代", "伐→代"),
    (r"瘀班", "瘀斑", "班→斑"),
    (r"紫班", "紫斑", "班→斑"),
    (r"窒闷", "窒闷", "保留"),
    (r"胸窒", "胸窒", "保留"),
    (r"蒌", "蒌", "保留（瓜蒌）"),
]

# ===== 方名 OCR 误写对照 =====
FANGMING = {
    "参付汤": "参附汤",
    "参付龙牡汤": "参附龙牡汤",
    "瓜娄薤白半夏汤": "瓜蒌薤白半夏汤",
    "瓜娄薤白白酒汤": "瓜蒌薤白白酒汤",
    "薤百": "薤白",
    "炙甘早汤": "炙甘草汤",
    "归牌汤": "归脾汤",
    "朱砂安神九": "朱砂安神丸",
    "酸枣人汤": "酸枣仁汤",
    "桂枝甘早龙骨牡蛎汤": "桂枝甘草龙骨牡蛎汤",

}

# ===== 药名 OCR 误写对照（仅收录明确 OCR 误写，不收录合法缩写如"熟地""生地"）=====
YAOMING = {
    "瓜娄": "瓜蒌",
    "薤百": "薤白",
    "半下": "半夏",
    "茯令": "茯苓",
    "白朱": "白术",
    "白朮": "白术",
    "黄苓": "黄芩",
    "黄莲": "黄连",
    "黄茋": "黄芪",
    "黄蓍": "黄芪",
    "党叁": "党参",
    "人叁": "人参",
    "丹叁": "丹参",
    "玄叁": "玄参",
    "苦叁": "苦参",
    "沙叁": "沙参",
    "当回": "当归",
    "白勺": "白芍",
    "赤勺": "赤芍",
    "厚卜": "厚朴",
    "沉乡": "沉香",

}

# ===== 脉象名 =====
MAIXIANG = ["浮", "沉", "迟", "数", "滑", "涩", "虚", "实", "长", "短",
            "洪", "细", "微", "濡", "弱", "缓", "弦", "紧", "结", "代",
            "促", "动", "革", "牢", "散", "芤", "伏"]

# ===== 舌象名 =====
SHEXIANG = ["淡红", "淡白", "红", "绛", "紫", "紫暗", "青", "胖", "瘦",
            "齿痕", "裂纹", "芒刺", "薄白", "薄黄", "白腻", "黄腻", "白滑",
            "黄燥", "剥", "少苔", "无苔", "光剥"]


def strip_html(html):
    """去除 HTML 标签，保留纯文本"""
    return re.sub(r'<[^>]+>', ' ', html)


def check_xingjin(text):
    """形近字检查（上下文模式匹配，仅报告高度可疑的误识）"""
    results = []
    for pattern, correct, note in XINGJIN_PATTERNS:
        if "保留" in note:
            continue  # 跳过正确写法的占位项
        for m in re.finditer(pattern, text):
            start = max(0, m.start() - 10)
            end = min(len(text), m.end() + 10)
            ctx = text[start:end].replace('\n', ' ')
            results.append(("形近字", m.group(), correct, ctx))
    return results


def check_fangming(text):
    """方名核对"""
    results = []
    for wrong, right in FANGMING.items():
        if wrong == right:
            continue
        if wrong in text:
            # 找到上下文
            idx = text.find(wrong)
            start = max(0, idx - 10)
            end = min(len(text), idx + len(wrong) + 10)
            ctx = text[start:end].replace('\n', ' ')
            results.append(("方名", wrong, right, ctx))
    return results


def check_yaoming(text):
    """药名核对"""
    results = []
    for wrong, right in YAOMING.items():
        if wrong == right:
            continue
        if wrong in text:
            idx = text.find(wrong)
            start = max(0, idx - 8)
            end = min(len(text), idx + len(wrong) + 8)
            ctx = text[start:end].replace('\n', ' ')
            results.append(("药名", wrong, right, ctx))
    return results


def check_maixiang(text):
    """脉象名检查：检查常见误写"""
    results = []
    # 常见脉象误写
    maixiang_errors = {
        "结伐": "结代",
        "玄脉": "弦脉",
        "儒脉": "濡脉",
        "浮紧或玄紧": "浮紧或弦紧",
    }
    for wrong, right in maixiang_errors.items():
        if wrong in text:
            idx = text.find(wrong)
            start = max(0, idx - 10)
            end = min(len(text), idx + len(wrong) + 10)
            ctx = text[start:end].replace('\n', ' ')
            results.append(("脉象", wrong, right, ctx))
    return results


def check_shexiang(text):
    """舌象名检查（仅报告明确 OCR 误写，"舌暗紫""舌紫暗"均合法不报告）"""
    results = []
    shexiang_errors = {
        "紫甘": "紫暗",
        "舌苔少": "少苔",
    }
    for wrong, right in shexiang_errors.items():
        if wrong == right:
            continue
        if wrong in text:
            idx = text.find(wrong)
            start = max(0, idx - 10)
            end = min(len(text), idx + len(wrong) + 10)
            ctx = text[start:end].replace('\n', ' ')
            results.append(("舌象", wrong, right, ctx))
    return results


def check_duplicate_punct(text):
    """检查异常重复标点（可能漏字/串行）"""
    results = []
    for m in re.finditer(r'([，。、；：]){2,}', text):
        start = max(0, m.start() - 10)
        end = min(len(text), m.end() + 10)
        ctx = text[start:end].replace('\n', ' ')
        results.append(("重复标点", m.group(), "检查是否漏字/串行", ctx))
    return results


def check_strange_spaces(text):
    """检查中文字符间的 2+ 连续半角空格（可能是 OCR 串行/排版异常）。
    单个半角空格、全角空格（　）和换行符视为正常，不报告。"""
    results = []
    for m in re.finditer(r'[\u4e00-\u9fff][ \t]{2,}[\u4e00-\u9fff]', text):
        start = max(0, m.start() - 5)
        end = min(len(text), m.end() + 5)
        ctx = text[start:end].replace('\n', '↵')
        results.append(("连续半角空格", m.group(), "检查是否 OCR 串行", ctx))
    return results


def main():
    parser = argparse.ArgumentParser(description="中医内科学 OCR 错字核验")
    parser.add_argument("file", nargs="?", help="待查文件路径（.txt 或 .html）")
    parser.add_argument("--text", help="直接检查文本")
    args = parser.parse_args()

    if args.text:
        raw = args.text
    elif args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"文件不存在: {p}")
            sys.exit(1)
        raw = p.read_text(encoding="utf-8", errors="replace")
    else:
        parser.print_help()
        sys.exit(1)

    text = strip_html(raw)

    print(f"文本长度: {len(text)} 字符")
    print("=" * 60)

    all_results = []
    all_results.extend(check_xingjin(text))
    all_results.extend(check_fangming(text))
    all_results.extend(check_yaoming(text))
    all_results.extend(check_maixiang(text))
    all_results.extend(check_shexiang(text))
    all_results.extend(check_duplicate_punct(text))
    all_results.extend(check_strange_spaces(text))

    if not all_results:
        print("✓ 未发现疑似 OCR 错字")
    else:
        print(f"发现 {len(all_results)} 处疑似 OCR 错字：")
        print("-" * 60)
        for i, (cat, wrong, right, ctx) in enumerate(all_results, 1):
            print(f"{i}. [{cat}] 「{wrong}」 → 疑似「{right}」")
            print(f"   上下文: …{ctx}…")
            print()
        print("提示：以上仅为可疑项，请回搜教材原文确认后再改。")


if __name__ == "__main__":
    main()
