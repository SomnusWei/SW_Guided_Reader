#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中医讲义HTML质量验证脚本
用法：python validate_html.py 讲义.html
"""

import re
import sys
import os


def validate_html(filepath):
    """验证HTML文件的基本质量"""
    results = {
        "P0": [],  # 致命错误
        "P1": [],  # 重要问题
        "P2": [],   # 一般问题
    }

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
        lines = content.split('\n')

    print(f"📄 正在验证: {filepath}")
    print(f"   文件大小: {len(content)} 字符, {len(lines)} 行")
    print()

    # ========== P0 级检查 ==========

    # 1. 基本标签闭合
    if '</body>' not in content:
        results["P0"].append("缺少 </body> 标签")
    if '</html>' not in content:
        results["P0"].append("缺少 </html> 标签")

    # 2. div标签配对（粗略检查）
    open_div = len(re.findall(r'<div\s', content))
    close_div = len(re.findall(r'</div>', content))
    if abs(open_div - close_div) > 5:  # 允许少量差异（有些div可能在style中）
        results["P0"].append(f"div标签不匹配: 开{open_div} / 关{close_div} (差值过大)")

    # 3. table标签配对
    open_table = len(re.findall(r'<table', content))
    close_table = len(re.findall(r'</table>', content))
    if open_table != close_table:
        results["P0"].append(f"table标签不匹配: 开{open_table} / 关{close_table}")

    # 4. 毒性药物检查
    toxic_herbs = ["附子", "川乌", "草乌", "朱砂", "雄黄", "马钱子", "细辛"]
    for herb in toxic_herbs:
        count = content.count(herb)
        if count > 0:
            # 检查是否有"使用注意"、"慎用"、"忌用"、"禁忌"等词附近
            # （简化检查：只提示，不判定为错误）
            pass

    # ========== P1 级检查 ==========

    # 5. 锚点匹配
    href_pattern = re.compile(r'href="#([^"]+)"')
    id_pattern = re.compile(r'id="([^"]+)"')
    hrefs = set(href_pattern.findall(content))
    ids = set(id_pattern.findall(content))

    missing_ids = hrefs - ids
    unused_ids = ids - hrefs

    if missing_ids:
        # 过滤掉javascript:void(0)等特殊值
        real_missing = {h for h in missing_ids if not h.startswith(('javascript', '#'))}
        if real_missing:
            results["P1"].append(f"目录链接失效: {len(real_missing)}个锚点找不到 (如: {list(real_missing)[:3]})")

    # 6. 目录是否存在
    if 'class="toc"' not in content and 'class="toc"' not in content:
        results["P1"].append("未找到目录 (.toc)")

    # 7. 章节结构
    chapter_count = len(re.findall(r'class="chapter-header"', content))
    if chapter_count == 0:
        results["P1"].append("未找到章节标题 (.chapter-header)")
    else:
        results["P2"].append(f"章节数量: {chapter_count}")

    # 8. 附录是否存在
    if '附录' not in content:
        results["P1"].append("未找到附录部分")

    # ========== P2 级检查 ==========

    # 9. 常见OCR错字
    common_typos = {
        "黄苓": "应为'黄芩'",
        "白木": "可能是'白术'",
        "灸甘草": "应为'炙甘草'",
        "羌话": "应为'羌活'",
        "蒿本": "应为'藁本'",
        "伏苓": "应为'茯苓'",
        "半厦": "应为'半夏'",
        "构杞": "应为'枸杞'",
        "黄茋": "应为'黄芪'",
        "土炒白朮": "应为'土炒白术'",
    }
    typo_found = []
    for typo, suggestion in common_typos.items():
        if typo in content:
            count = content.count(typo)
            typo_found.append(f"{typo}({suggestion}) x{count}")

    if typo_found:
        results["P2"].append(f"可能的OCR错字: {'; '.join(typo_found[:5])}")

    # 10. 主题色使用
    primary_matches = re.findall(r'--primary:\s*([^;]+)', content)
    if primary_matches:
        results["P2"].append(f"主题色: {primary_matches[0].strip()}")
    else:
        results["P2"].append("未使用CSS变量主题色")

    # 11. 字体回退
    if 'SimSun' in content and 'Noto' not in content and 'Songti' not in content:
        results["P2"].append("字体可能缺少跨平台回退（只有SimSun）")

    # ========== 输出结果 ==========

    print("=" * 60)
    print("验证结果")
    print("=" * 60)

    for level, label, color in [
        ("P0", "致命错误", "🔴"),
        ("P1", "重要问题", "🟡"),
        ("P2", "一般问题/信息", "🟢"),
    ]:
        items = results[level]
        print(f"\n{color} {label} ({len(items)}项):")
        if items:
            for item in items:
                print(f"   • {item}")
        else:
            print("   ✅ 无")

    # 统计
    p0 = len(results["P0"])
    p1 = len(results["P1"])
    p2 = len(results["P2"])

    print()
    print("-" * 60)
    print(f"总计: P0={p0}, P1={p1}, P2={p2}")

    if p0 > 0:
        print("❌ 存在致命错误，必须修复后才能使用")
        return 1
    elif p1 > 3:
        print("⚠️  存在较多重要问题，建议修复后使用")
        return 0
    else:
        print("✅ 基本通过验证，可以使用")
        return 0


def main():
    if len(sys.argv) < 2:
        print("用法: python validate_html.py <HTML文件路径>")
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    exit_code = validate_html(filepath)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
