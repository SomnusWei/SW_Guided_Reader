#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
讲义 HTML 专家引用时间戳清洗器（yixue-zonghe 技能）

去除讲义中形如 `P## [hh:mm:ss]` 的讲次时间戳标注，并清理删除后遗留的空格与冗余说明文字。
同时兼容两种形态：
  · 网页版：<span class="ts">P01 [00:12:34]</span>
  · OneNote 版：<span style="font-family:Consolas,monospace;font-size:9pt;
                  background-color:#f6e7cd;color:#7a4b00;font-weight:bold;
                  white-space:nowrap;">P01 [00:12:34]</span>
以及未被 span 包裹的裸文本形式。

用法：
    python strip_timestamps.py --in 讲义.html --out 讲义_无戳版.html
    python strip_timestamps.py --in 讲义.html --inplace
    python strip_timestamps.py --in 讲义.html --dry-run      # 只报告不改

调用方也可作为库使用：
    from strip_timestamps import strip_timestamps
    new_html, report = strip_timestamps(html)
"""
import argparse
import os
import re
import sys
import io

# ---------------------------------------------------------------- 正则
# 形如 P01 [00:12:34] / P08 [00:04:18–00:04:32] / P01 [00:10:55 / 00:15:16]
TS_BODY = r'P\d\d\s*\[\s*\d{1,2}:\d{2}:\d{2}[^\]]*\]'
# 同一 span 内常并列多个时间戳，用「 / 」分隔，务必整体匹配，
# 否则（只匹配首个再要求紧跟 </span>）整段会被漏删。续段可省略讲次前缀：
#   P03 [00:57:19–00:59:53] / P04 [00:04:28–00:05:05]   ← 续段带 P##
#   P09 [00:12:28–00:12:45] / [00:39:38–00:42:15]        ← 续段不带 P##
TS_ITEM = r'(?:P\d\d\s*)?\[\s*\d{1,2}:\d{2}:\d{2}[^\]]*\]'
TS_SEQ = TS_BODY + r'(?:\s*/\s*' + TS_ITEM + r')*'

TS_SPAN_WEB = re.compile(
    r'<span\s+class="ts"[^>]*>\s*' + TS_SEQ + r'\s*</span>')
# OneNote 版：Consolas 内联 span，且内容整体是时间戳
TS_SPAN_ONE = re.compile(
    r'<span\s+style="font-family:Consolas,monospace[^"]*"[^>]*>\s*' + TS_SEQ + r'\s*</span>')
TS_BARE = re.compile(TS_BODY)
# 自检用：任何「[hh:mm:ss…]」括号形式（含无讲次前缀的续段）都算残留
TS_ANY = re.compile(r'\[\s*\d{1,2}:\d{2}:\d{2}[^\]]*\]')
# 用作示例记法的字面量（不是真实时间戳，但用户要求一并清除）
TS_LEGEND = re.compile(
    r'<span\s+style="font-family:Consolas,monospace[^"]*"[^>]*>\s*P##\s*\[hh:mm:ss\]\s*</span>')
TS_ANY_P = re.compile(r'P##\s*\[hh:mm:ss\]')

# 说明文字里解释该记法的整句：删除记法后这些句子会变成残句，须同步改写
CODE_LEGEND = (r'<span\s+style="font-family:Consolas,monospace[^"]*"[^>]*>\s*'
               r'P##\s*\[hh:mm:ss\]\s*</span>')
LEGEND_REWRITES = [
    # 封面 src 行尾的「· 全部引用带 P## [hh:mm:ss] 出处」
    (re.compile(r'\s*·\s*全部引用带\s*' + CODE_LEGEND + r'\s*出处'), ''),
    # 使用说明卡片里那条 li 的开头
    (re.compile(r'所有专家引用均带\s*' + CODE_LEGEND + r'\s*出处，可回溯至原始录音。'),
     '全部专家内容源自原始录音转写稿。'),
]

# 删除时间戳后，若紧邻这些字符则连同左侧空白一起删掉
TIGHT_AFTER = '。，、；：）」』！？'
SP = ' \t'


def _removal_span(s, a, b):
    """给定待删区间 [a,b)，把紧邻的空白并入，返回扩展后的区间"""
    left = 0
    while a - left > 0 and s[a - left - 1] in SP:
        left += 1
    return a - left, b


def strip_timestamps(html):
    """返回 (新 html, 报告 dict)"""
    rep = {
        'span_web': 0, 'span_onenote': 0, 'bare': 0,
        'legend_marks': 0, 'legend_rewrites': [], 'legend_missing': [],
        'space_fixes': 0,
    }

    # 1) 先改写说明文字里解释该记法的整句（必须在删 span 之前，否则精确匹配失效）
    for pat, new in LEGEND_REWRITES:
        html, n = pat.subn(new, html, count=1)
        if n:
            rep['legend_rewrites'].append(new or '(删除该短语)')
        else:
            rep['legend_missing'].append(pat.pattern[:56])

    # 2) 删除带 span 的时间戳（先网页版形态，再 OneNote 形态）
    for pat, key in ((TS_SPAN_WEB, 'span_web'), (TS_SPAN_ONE, 'span_onenote')):
        while True:
            m = pat.search(html)
            if not m:
                break
            a, b = _removal_span(html, m.start(), m.end())
            html = html[:a] + html[b:]
            rep[key] += 1

    # 3) 清掉残留的记法示例 span（改写未覆盖到的情形）
    while True:
        m = TS_LEGEND.search(html)
        if not m:
            break
        a, b = _removal_span(html, m.start(), m.end())
        html = html[:a] + html[b:]
        rep['legend_marks'] += 1

    # 4) 收尾：标点/标签前不留多余空格；连续空白折叠为一个
    before = html
    html = re.sub(r'[ \t]+(?=[。，、；：）」』！？]|</p>|</td>|</li>)', '', html)
    html = re.sub(r'[ \t]{2,}', ' ', html)
    rep['space_fixes'] = len(before) - len(html)

    return html, rep


def main():
    ap = argparse.ArgumentParser(description='去除讲义中 P## [hh:mm:ss] 时间戳标注')
    ap.add_argument('--in', dest='src', required=True, help='输入 HTML')
    ap.add_argument('--out', dest='dst', default=None, help='输出 HTML（与 --inplace 二选一）')
    ap.add_argument('--inplace', action='store_true', help='原地改写输入文件')
    ap.add_argument('--dry-run', action='store_true', help='只统计与预演，不写文件')
    ap.add_argument('--check', action='store_true', help='清洗后自检并打印报告')
    a = ap.parse_args()

    if not a.inplace and not a.dst and not a.dry_run:
        ap.error('请指定 --out、--inplace 或 --dry-run 之一')

    raw = open(a.src, encoding='utf-8').read()
    out, rep = strip_timestamps(raw)

    print(f'[strip_timestamps] {a.src}')
    print(f'  span(网页版)      {rep["span_web"]}')
    print(f'  span(OneNote版)   {rep["span_onenote"]}')
    print(f'  裸文本            {rep["bare"]}')
    print(f'  记法示例 span     {rep["legend_marks"]}')
    for r in rep['legend_rewrites']:
        print(f'  说明文字已改写 →  {r}')
    for m in rep['legend_missing']:
        print(f'  （提示）文中无此说明句，跳过：{m}…')
    print(f'  空白收尾          {rep["space_fixes"]} 字符')
    print(f'  字符数 {len(raw)} → {len(out)}（-{len(raw) - len(out)}）')

    if a.check:
        print('\n== 自检 ==')
        left = len(TS_ANY_P.findall(out)) + len(TS_ANY.findall(out))
        print(f'  {"OK " if left == 0 else "!! "}残留 P##[..] / 时间戳: {left}')
        for pat, desc in (('class="ts"', '.ts class 残留'),
                          ('#f6e7cd', '时间戳底色残留')):
            n = out.count(pat)
            print(f'  {"OK " if n == 0 else "!! "}{desc}: {n}')
        for tag in ('table', 'tr', 'td', 'th', 'p', 'span', 'ul', 'ol', 'li'):
            o = len(re.findall(rf'<{tag}[ >]', out))
            c = len(re.findall(rf'</{tag}>', out))
            if o != c:
                print(f'  !! {tag} 标签不匹配: open={o} close={c}')
        print('  标签配对检查完毕')

    if a.dry_run:
        print('\n[dry-run] 未写入任何文件')
        return
    target = a.src if a.inplace else a.dst
    open(target, 'w', encoding='utf-8').write(out)
    print(f'\n已写出 {target}')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
