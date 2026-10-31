#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中医知识库索引生成脚本
扫描教材目录（700+古籍 + 现代教材），按类目分类生成 references/textbook-index.md
用法：python build_textbook_index.py --textbook-dir "F:/work/中医学skill/教材" --output references/textbook-index.md
"""

import os
import re
import argparse
from datetime import datetime

# ============ 现代教材（核心层，精确匹配） ============
MODERN_TEXTBOOKS = [
    "中医基础理论", "中医诊断学", "中药学", "方剂学", "中医内科学",
    "经络腧穴学", "针灸治疗学", "石学敏针灸全集_第2版", "中医大辞典(第2版)", "《黄帝内经》",
]

# ============ 个别文件的强制归类（优先级最高） ============
EXPLICIT = {
    "小儿推拿广意": "儿科",
    "厘正按摩要术": "针灸推拿",
    "脚气治法总要": "综合医书",
    "症因脉治": "综合医书",
    "脉因证治": "综合医书",
    "青囊秘诀": "外科",
    "灵药秘方": "外科",
    "卫济宝书": "外科",
    "刘涓子鬼遗方": "外科",
    "千金宝要": "方书",
    "急救广生集": "方书",
    "女丹合编选注": "养生",
    "巢氏病源补养宣导法": "养生",
    "三消论": "综合医书",
    "中国医籍考": "其他",
    "洗冤集录": "其他",
    "思考中医": "综合医书",
    "中医之钥": "综合医书",
    "澄空民间中医学精髓论": "综合医书",
    "寿世保元": "综合医书",
    "万病回春": "综合医书",
    "杨成博先生遗留穴道秘书": "伤科",
    "鲙残篇": "医案医话",
    "推逢寤语 医林琐语": "医案医话",
    "医中一得 医医十病": "医案医话",
    "西池集": "其他",
    "心医集": "综合医书",
    "性命要旨": "养生",
    "十二經補瀉溫涼引經藥歌": "本草",
    "十剂表": "方书",
    "评琴书屋医略": "综合医书",
    "理瀹骈文": "针灸推拿",
    # —— 第二轮人工校订补充 ——
    "审视瑶函": "眼喉口齿",
    "重楼玉钥": "眼喉口齿",
    "本经逢原": "本草",
    "原要论": "儿科",
    "邯郸遗稿": "妇产科",
    "济生集": "妇产科",
    "回生集": "方书",
    "绛囊撮要": "方书",
    "串雅内外编": "方书",
    "李翰卿": "医案医话",
    "市隐庐医学杂着": "医案医话",
    "奇症汇": "医案医话",
    "医灯续焰": "脉学诊法",
    "运气要诀": "医经",
    "六因条辨": "温病瘟疫",
    "热病衡正": "温病瘟疫",
    "万氏秘传片玉心书": "儿科",
    "类证活人书": "伤寒论",
    "中寒论辩证广注": "伤寒论",
    "华氏中藏经": "医经",
    "阴证略例": "伤寒论",
    "增订十药神书": "综合医书",
    "医学课儿策": "综合医书",
}

# ============ 类目及匹配规则（按顺序匹配，先中先得） ============
CATEGORY_RULES = [
    ("针灸推拿", ["针灸", "针经", "灸法", "灸经", "灸膏肓", "神灸", "神应经", "经络", "经穴",
                  "腧穴", "推拿", "按摩", "子午流注", "刺血", "刺灸", "铜人指穴", "明堂",
                  "十四经", "经脉", "奇经八脉", "穴道", "金针", "针方?"]),
    ("伤寒论", ["伤寒"]),
    ("金匮要略", ["金匮"]),
    ("温病瘟疫", ["温病", "温热", "温疫", "瘟疫", "疫疹", "疫", "时病", "暑", "湿热",
                  "霍乱", "瘴", "痧胀", "伏气", "广温", "说疫"]),
    ("医经", ["内经", "素问", "灵枢", "难经", "类经", "太素", "医经", "外经", "灵素",
              "医经读", "医效秘传"]),
    ("脉学诊法", ["脉", "望诊", "舌", "察病", "形色外诊", "诊家", "诊宗", "三指禅",
                  "四诊", "临症验舌"]),
    ("本草", ["本草", "药性", "药征", "药鉴", "炮炙", "炮制", "食疗", "食治", "食鉴",
              "饮膳", "别录", "珍珠囊", "得配", "要药", "药症", "草药", "药解", "药歌",
              "食性", "食疗方"]),
    ("妇产科", ["女科", "妇科", "妇人", "产科", "胎产", "产宝", "济阴", "广嗣", "达生",
                "宜麟", "宁坤", "毓麟", "产鉴", "产后", "竹林女科", "女科"]),
    ("儿科", ["小儿", "幼科", "婴", "痘疹", "麻疹", "麻科", "儿科", "颅囟", "幼幼",
              "保婴", "活幼", "慈幼", "痧疹", "种痘", "保幼", "幼童", "麻痧"]),
    ("眼喉口齿", ["眼科", "目科", "目经", "银海", "喉科", "喉症", "喉舌", "口齿", "明目",
                  "原机启微", "一草亭", "走马急疳", "异授眼科", "喉", "眼科奇书"]),
    ("外科", ["外科", "疡", "疮", "疽", "痈", "发背", "疯门", "解围", "疠", "背疽",
              "外科心法", "秘传外科", "集验背疽"]),
    ("伤科", ["跌打", "正骨", "伤科", "接骨", "金疮", "救伤", "跌损", "正体", "损伤"]),
    ("医案医话", ["医案", "医话", "方案真本", "临证指南", "寓意草", "名医类案", "回春录",
                  "经验集", "医验", "验案", "垂教", "医论选", "临证经验", "医说",
                  "经验选", "经验录", "医案论", "医疗经验"]),
    ("方书", ["方", "汤头", "局方", "剂", "祖剂"]),
    ("养生", ["养生", "导引", "易筋", "寿世", "仙经", "修昆仑", "养老", "饮食须知",
              "洗髓", "延年"]),
]

# ============ 类目描述（写入索引文件） ============
CATEGORY_DESC = {
    "现代教材": "十四五/统编现代教材，讲义制作与考试出题的核心依据，观点权威、体系规范",
    "医经": "《黄帝内经》《难经》及其注本、类编，理论溯源与原文引用的源头",
    "伤寒论": "《伤寒论》历代注本与发挥，六经辨证、经方讲解的核心文献",
    "金匮要略": "《金匮要略》注本与方歌，杂病辨证与经方文献",
    "温病瘟疫": "温病学派与瘟疫、湿热、痧胀、霍乱类专书，卫气营血/三焦辨证文献",
    "本草": "历代本草、药性、炮制、食疗文献，中药讲解与药性查询的依据",
    "方书": "历代方书、方论、歌诀，方剂组成出处考证的依据",
    "针灸推拿": "针灸、经络、腧穴、刺灸法、推拿、外治文献，针灸模块的核心依据",
    "脉学诊法": "脉学、望诊、舌诊等诊断文献，四诊教学素材",
    "妇产科": "妇科、产科、胎产文献",
    "儿科": "儿科、痘疹麻疹文献",
    "眼喉口齿": "眼科、喉科、口齿专科文献",
    "外科": "外科疮疡文献",
    "伤科": "跌打损伤、正骨文献",
    "医案医话": "历代医案、医话、医论、临证经验，病案分析的核心素材",
    "综合医书": "历代综合医书与内科杂病专著，讲义总论与辨证论治素材",
    "养生": "养生、导引、食疗文献",
    "其他": "法医、目录学等辅助文献",
}

# 类目显示顺序
CATEGORY_ORDER = [
    "现代教材", "医经", "伤寒论", "金匮要略", "温病瘟疫", "本草", "方书",
    "针灸推拿", "脉学诊法", "妇产科", "儿科", "眼喉口齿", "外科", "伤科",
    "医案医话", "综合医书", "养生", "其他",
]


def classify(filename):
    """对单个文件名分类：先精确匹配现代教材，再查强制表，再按规则顺序匹配"""
    stem = os.path.splitext(filename)[0]
    # 去掉编号前缀
    name = re.sub(r"^\d+[\.\-]", "", stem)
    # 现代教材精确匹配
    for mt in MODERN_TEXTBOOKS:
        if name == mt or name == f"《{mt}》" or mt == name:
            return "现代教材"
    # 强制归类
    for key, cat in EXPLICIT.items():
        if key in name:
            return cat
    # 规则顺序匹配
    for cat, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in name:
                return cat
    return "综合医书"


def human_size(size):
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    return f"{size / 1024:.0f}KB"


def main():
    parser = argparse.ArgumentParser(description="中医知识库索引生成")
    parser.add_argument("--textbook-dir", default=None,
        help="教材目录（不指定则按 --track 自动选择）")
    parser.add_argument("--track", choices=["中医", "西医"], default="中医",
        help="医学体系：中医/西医（默认中医）")
    parser.add_argument("--output", default="references/textbook-index.md", help="输出索引文件")
    args = parser.parse_args()
    if args.textbook_dir is None:
        sub = "中医教材" if args.track == "中医" else "西医教材"
        args.textbook_dir = os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", sub))

    # 收集所有 txt，并标记是否有同名 PDF（来源于 PDF 转写）
    all_files = os.listdir(args.textbook_dir)
    pdf_stems = {os.path.splitext(f)[0] for f in all_files if f.lower().endswith(".pdf")}

    files = sorted(
        f for f in all_files
        if f.lower().endswith(".txt")
    )
    result = {cat: [] for cat in CATEGORY_ORDER}
    pdf_count = 0
    for f in files:
        cat = classify(f)
        size = os.path.getsize(os.path.join(args.textbook_dir, f))
        stem = os.path.splitext(f)[0]
        from_pdf = stem in pdf_stems
        if from_pdf:
            pdf_count += 1
        result[cat].append((f, size, from_pdf))

    lines = []
    lines.append("# 中医知识库总索引（教材目录全量）")
    lines.append("")
    lines.append(f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}，"
                 f"共 {len(files)} 个文本文件"
                 f"（其中 {pdf_count} 个由 PDF 自动转写）。"
                 f"重建索引：`python scripts/build_textbook_index.py`"
                 f"｜同步新增 PDF：`python scripts/sync_new_materials.py`")
    lines.append("> 检索方法：先按类目锁定文件，再用 `scripts/search_textbooks.py` 或 Grep 在指定文件内搜关键词。")
    lines.append("")
    lines.append("## 类目总览")
    lines.append("")
    lines.append("| 类目 | 数量 | 主要用途 |")
    lines.append("|------|-----:|----------|")
    for cat in CATEGORY_ORDER:
        if result[cat]:
            lines.append(f"| {cat} | {len(result[cat])} | {CATEGORY_DESC[cat]} |")
    lines.append("")

    for cat in CATEGORY_ORDER:
        if not result[cat]:
            continue
        lines.append(f"## {cat}（{len(result[cat])}部）")
        lines.append("")
        lines.append(f"> {CATEGORY_DESC[cat]}")
        lines.append("")
        for f, size, from_pdf in result[cat]:
            tag = " `[PDF转]`" if from_pdf else ""
            lines.append(f"- `{f}`（{human_size(size)}）{tag}")
        lines.append("")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"[OK] 索引已生成: {args.output}  (txt={len(files)}, from_pdf={pdf_count})")
    for cat in CATEGORY_ORDER:
        if result[cat]:
            print(f"   {cat}: {len(result[cat])}")


if __name__ == "__main__":
    main()
