#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中医讲义素材提取脚本（升级版：711 文件知识库）

核心改进（相对 tcm-lecture-builder 原型）：
  1. 编码兼容 —— 通过 text_utils 自动处理 UTF-8 教材 + GB18030 古籍
  2. 关键词表动态解析 —— 从 references/keyword-mapping.md 读取 8 大系统关键词，不再硬编码
  3. 古籍检索 —— --ancient 开关启用 701 部古籍经典文献提取，支持 --category 类目过滤
  4. 自定义关键词 —— --keyword 可脱离系统表自由提取

用法示例：
  # 按系统提取（现代教材，标准深度）
  python extract_content.py --system 肺系 --textbook-dir "F:/work/中医学skill/教材" --output-dir 讲义素材

  # 深度模式：现代教材 + 全部古籍
  python extract_content.py --system 心系 --depth deep --ancient --textbook-dir "F:/work/中医学skill/教材"

  # 深度模式 + 只搜伤寒/金匮类古籍
  python extract_content.py --system 脾胃系 --depth deep --ancient --category 伤寒论,金匮要略

  # 自定义关键词（不依赖系统表）
  python extract_content.py --keyword 桂枝汤,胸痹 --ancient --category 金匮要略
"""

import os
import re
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from text_utils import read_text_lines, clean_ancient_markup  # noqa: E402

# 默认教材目录 = 与本 skill 同级的「教材」资料夹（安装时把教材放到 skill 根目录下，
# 即与 SKILL.md 同一文件夹；不同位置可用 --textbook-dir 覆盖）
def _default_textbook_dir(track="中医"):
    sub = "中医教材" if track == "中医" else "西医教材"
    return os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", sub))

TRACK_CHOICES = ["中医", "西医"]
KEYWORD_MAPPING_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "references", "keyword-mapping.md")

# 现代教材文件映射
TEXTBOOKS = {
    "neike": "中医内科学.txt",
    "fangji": "方剂学.txt",
    "zhongyao": "中药学.txt",
    "jichu": "中医基础理论.txt",
    "zhenduan": "中医诊断学.txt",
    "jingluo": "经络腧穴学.txt",
    "zhenjiu": "针灸治疗学.txt",
    "shixue": "石学敏针灸全集_第2版.txt",
    "dacidian": "中医大辞典(第2版).txt",
    "neijing": "《黄帝内经》.txt",
}

# 教材分层策略
TEXTBOOK_LAYERS = {
    "core": ["neike", "fangji", "zhongyao"],
    "support": ["jichu", "zhenduan"],
    "extend": ["jingluo", "zhenjiu", "shixue"],
    "reference": ["dacidian", "neijing"],
}

# 类目 -> 文件名关键词（与 build_textbook_index.py 一致，简化版）
CATEGORY_HINTS = {
    "伤寒论": ["伤寒"],
    "金匮要略": ["金匮"],
    "温病瘟疫": ["温病", "温热", "温疫", "瘟疫", "湿热", "霍乱", "时病"],
    "本草": ["本草", "药性", "药征", "炮炙", "食疗", "食治", "别录", "得配"],
    "针灸推拿": ["针灸", "针经", "灸", "经络", "经穴", "腧穴", "推拿", "按摩", "子午流注", "十四经"],
    "医经": ["内经", "素问", "灵枢", "难经", "类经", "太素", "外经"],
    "医案医话": ["医案", "医话", "临证", "经验", "验案", "寓意草", "医说"],
    "方书": ["方", "汤头", "局方", "剂"],
    "妇产科": ["妇", "产", "经产", "女科"],
    "儿科": ["儿科", "小儿", "幼科", "幼幼"],
}

# 关键词类别 -> 输出模块映射
CATEGORY_MODULE_MAP = {
    "疾病名": "疾病概论",
    "证型名": "辨证论治",
    "代表方剂": "方剂详解",
    "核心中药": "中药详解",
    "理论关键词": "基础理论",
}


def parse_keyword_mapping(mapping_path):
    """从 references/keyword-mapping.md 解析 8 大系统关键词表。

    返回: {系统名: {类别: [关键词, ...]}}
    例如 {"肺系": {"疾病名": ["感冒", ...], "代表方剂": [...]}}
    """
    systems = {}
    if not os.path.exists(mapping_path):
        return systems
    lines = read_text_lines(mapping_path)
    current_system = None
    for line in lines:
        line = line.rstrip("\n")
        m = re.match(r"^##\s+[一二三四五六七八九十]+、(\S+)病证", line)
        if m:
            # "肺系病证" -> "肺系"；"脑系/神系病证" -> "脑系"
            name = m.group(1).split("/")[0]
            current_system = name
            systems.setdefault(current_system, {})
            continue
        if current_system is None:
            continue
        # 表格行: | **疾病名** | 感冒、咳嗽、... |
        m = re.match(r"^\|\s*\*\*(\S+)\*\*\s*\|\s*(.+?)\s*\|\s*$", line)
        if m:
            category, keywords_str = m.group(1), m.group(2)
            keywords = [k.strip() for k in re.split(r"[、,，]", keywords_str) if k.strip()]
            if keywords:
                systems[current_system][category] = keywords
    return systems


def match_category(filename, categories):
    """判断文件是否属于给定类目列表（用于古籍过滤）"""
    stem = os.path.splitext(filename)[0]
    name = re.sub(r"^\d+[\.\-]", "", stem)
    for cat in categories:
        if cat in CATEGORY_HINTS:
            for kw in CATEGORY_HINTS[cat]:
                if kw in name:
                    return True
    return False


def search_keywords(lines, keywords, context_before=30, context_after=30, dedup_gap=10):
    """在文本行中搜索关键词，返回匹配结果及上下文（去重）"""
    results = []
    matched_lines = set()
    for i, line in enumerate(lines):
        hit_kw = None
        for kw in keywords:
            if kw in line:
                hit_kw = kw
                break
        if hit_kw is None:
            continue
        if any(abs(i - ml) < dedup_gap for ml in matched_lines):
            continue
        matched_lines.add(i)
        start = max(0, i - context_before)
        end = min(len(lines), i + context_after)
        snippet = clean_ancient_markup("".join(lines[start:end])).strip()
        results.append({
            "keyword": hit_kw, "line": i + 1,
            "start_line": start + 1, "end_line": end,
            "snippet": snippet,
        })
    return results


def extract_from_textbook(tb_key, tb_path, kw_groups):
    """从单本现代教材按模块提取素材"""
    modules = {}
    lines = read_text_lines(tb_path)

    def collect(category_keys, module_name, before, after):
        keywords = []
        for ck in category_keys:
            keywords.extend(kw_groups.get(ck, []))
        if not keywords:
            return
        for r in search_keywords(lines, keywords, before, after):
            modules.setdefault(module_name, []).append(r)

    if tb_key == "neike":
        collect(["疾病名"], "疾病概论", 50, 50)
        collect(["疾病名", "证型名"], "辨证论治", 30, 50)
    elif tb_key == "fangji":
        collect(["代表方剂"], "方剂详解", 20, 60)
    elif tb_key == "zhongyao":
        collect(["核心中药"], "中药详解", 5, 40)
    elif tb_key == "jichu":
        collect(["理论关键词"], "基础理论", 10, 30)
    elif tb_key == "zhenduan":
        collect(["疾病名", "证型名"], "辨证论治", 15, 30)
    elif tb_key in ("jingluo", "zhenjiu", "shixue"):
        collect(["疾病名"], "针灸内容", 20, 50)
    elif tb_key == "dacidian":
        collect(["疾病名", "代表方剂"], "疾病概论", 5, 15)
    elif tb_key == "neijing":
        collect(["理论关键词"], "基础理论", 10, 30)
    return modules


def extract_from_ancient(textbook_dir, kw_groups, categories, max_per_module=200):
    """从古籍库提取经典文献素材（deep 模式）"""
    module = []
    keywords = []
    for ck in ("疾病名", "代表方剂", "证型名"):
        keywords.extend(kw_groups.get(ck, []))
    if not keywords:
        return module

    all_files = sorted(
        f for f in os.listdir(textbook_dir) if f.lower().endswith(".txt"))
    if categories:
        targets = [f for f in all_files if match_category(f, categories)]
    else:
        # 未指定类目时只搜经典核心类目，避免综合医书噪声
        targets = [f for f in all_files if match_category(
            f, ["伤寒论", "金匮要略", "温病瘟疫", "医经", "方书"])]

    for fname in targets:
        path = os.path.join(textbook_dir, fname)
        if os.path.getsize(path) < 500:
            continue
        lines = read_text_lines(path)
        hits = search_keywords(lines, keywords, 10, 20, dedup_gap=15)
        if hits:
            module.append({"textbook": fname, "hits": hits})
        if len(module) >= max_per_module // 5:
            break
    return module


def save_modules(modules, output_dir, system):
    """将提取的素材保存为文件"""
    os.makedirs(output_dir, exist_ok=True)
    total = 0
    for module_name, items in modules.items():
        if not items:
            continue
        if module_name == "经典文献":
            # 古籍素材单独格式
            filepath = os.path.join(output_dir, f"{system}_经典文献.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"=== {system} - 经典文献素材（古籍库） ===\n\n")
                for entry in items:
                    f.write(f"\n{'='*60}\n📚 来源: {entry['textbook']}\n{'='*60}\n\n")
                    for h in entry["hits"]:
                        f.write(f"--- 关键词: {h['keyword']} (第{h['line']}行) ---\n{h['snippet']}\n\n")
            print(f"💾 已保存: {system}_经典文献.txt ({len(items)} 部古籍)")
            total += sum(len(e["hits"]) for e in items)
            continue

        filename = f"{system}_{module_name}.txt"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"=== {system} - {module_name} 素材 ===\n\n")
            by_textbook = {}
            for item in items:
                by_textbook.setdefault(item["textbook"], []).append(item)
            for tb, tb_items in by_textbook.items():
                f.write(f"\n{'='*60}\n📚 来源: {tb}\n{'='*60}\n\n")
                for item in tb_items:
                    f.write(f"--- 关键词: {item['keyword']} (第{item['line']}行) ---\n")
                    f.write(f"位置: 第{item['start_line']}-{item['end_line']}行\n")
                    f.write(f"{item['snippet']}\n\n")
        print(f"💾 已保存: {filename} ({len(items)}条)")
        total += len(items)
    return total


def main():
    parser = argparse.ArgumentParser(description="中医讲义素材提取工具（711 文件知识库版）")
    parser.add_argument("--system", default="", help="目标系统 (如: 肺系、心系、脾胃系、肝胆系、肾系、脑系、气血津液、肢体经络)")
    parser.add_argument("--track", choices=["中医", "西医"], default="中医", help="医学体系：中医/西医（默认中医）")
    parser.add_argument("--keyword", default="", help="自定义关键词，逗号分隔（与 --system 二选一）")
    parser.add_argument("--depth", default="standard", choices=["basic", "standard", "deep"],
                        help="深度等级: basic/standard/deep (默认: standard)")
    parser.add_argument("--ancient", action="store_true",
                        help="检索古籍库（701 部，建议 depth=deep 时使用）")
    parser.add_argument("--category", default="", help="古籍类目过滤，逗号分隔（如: 伤寒论,金匮要略）")
    parser.add_argument("--textbook-dir", default=None, help="教材目录路径")
    parser.add_argument("--output-dir", default="讲义素材", help="输出目录路径")
    args = parser.parse_args()
    if args.textbook_dir is None:
        args.textbook_dir = _default_textbook_dir(args.track)

    if not args.system and not args.keyword:
        parser.error("必须指定 --system 或 --keyword 之一")

    print(f"🎯 目标系统: {args.system or '(自定义关键词)'}")
    print(f"📊 深度等级: {args.depth}")
    print(f"📁 教材目录: {args.textbook_dir}")
    print(f"📤 输出目录: {args.output_dir}")
    print()

    # 构建关键词组
    if args.system:
        all_systems = parse_keyword_mapping(KEYWORD_MAPPING_FILE)
        if args.system not in all_systems:
            available = "、".join(all_systems.keys())
            print(f"❌ 系统 '{args.system}' 未找到。可用系统: {available}")
            return
        kw_groups = all_systems[args.system]
        system_label = args.system
    else:
        kws = [k.strip() for k in args.keyword.split(",") if k.strip()]
        kw_groups = {"疾病名": kws}
        system_label = "自定义"

    # 确定提取的教材范围
    layers_to_extract = ["core", "support"]
    if args.depth == "basic":
        layers_to_extract = ["core"]
    elif args.depth == "deep":
        layers_to_extract.extend(["extend", "reference"])

    modules = {}
    for layer in layers_to_extract:
        for tb_key in TEXTBOOK_LAYERS[layer]:
            tb_file = TEXTBOOKS.get(tb_key)
            if not tb_file:
                continue
            tb_path = os.path.join(args.textbook_dir, tb_file)
            if not os.path.exists(tb_path):
                print(f"⚠️  教材文件不存在: {tb_file}")
                continue
            print(f"📖 正在提取: {tb_file}")
            tb_modules = extract_from_textbook(tb_key, tb_path, kw_groups)
            for name, items in tb_modules.items():
                enriched = [{"textbook": tb_file, **r} for r in items]
                modules.setdefault(name, []).extend(enriched)

    # 古籍检索
    if args.ancient:
        categories = [c.strip() for c in args.category.split(",") if c.strip()]
        print(f"📜 正在检索古籍库（类目: {args.category or '经典核心类目'}）...")
        ancient = extract_from_ancient(args.textbook_dir, kw_groups, categories)
        if ancient:
            modules["经典文献"] = ancient
            print(f"   命中 {len(ancient)} 部古籍")

    if not any(modules.values()):
        print("❌ 未提取到任何素材")
        return

    total = save_modules(modules, args.output_dir, system_label)
    print(f"\n✅ 提取完成！共 {total} 条素材")
    for name, items in modules.items():
        if items:
            if name == "经典文献":
                print(f"   - 经典文献: {len(items)} 部古籍")
            else:
                print(f"   - {name}: {len(items)} 条")


if __name__ == "__main__":
    main()
