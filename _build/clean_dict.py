# -*- coding: utf-8 -*-
"""
清理已导出的 JSON：
1) 去掉 MDX 排版残留（页码行、流水编号）
2) 去掉释义开头与词头重复的第一行
3) 折叠多于 1 个的空行
4) 重新统计 index.json
"""
import os, json, re

OUT = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/tools/dict/data'

RE_PAGE = re.compile(r'^《[^》]{1,20}》\s*第\s*\d+\s*页$')
RE_SERIAL = re.compile(r'^\d{6,10}$')
RE_NUMONLY = re.compile(r'^[\s\d\.．,，、;；:：\-—_]+$')


def clean(k: str, v: str) -> str:
    lines = v.split('\n')
    # 去尾部的页码 / 流水号 / 纯符号行
    while lines:
        t = lines[-1].strip()
        if not t or RE_PAGE.match(t) or RE_SERIAL.match(t) or RE_NUMONLY.match(t):
            lines.pop()
        else:
            break
    # 去开头的空行
    while lines and not lines[0].strip():
        lines.pop(0)
    # 去首行若是纯词头重复
    if lines and lines[0].strip() == k.strip():
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    # 全文中夹带的页码行也清掉
    lines = [ln for ln in lines if not RE_PAGE.match(ln.strip())]
    # 折叠多空行
    out, blank = [], 0
    for ln in lines:
        s = ln.rstrip()
        if s:
            out.append(s); blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append('')
    txt = '\n'.join(out).strip()
    return txt


def main():
    idx = json.load(open(os.path.join(OUT, 'index.json'), encoding='utf-8'))
    for d in idx:
        fp = os.path.join(OUT, d['file'])
        arr = json.load(open(fp, encoding='utf-8'))
        new = []
        for k, v in arr:
            nv = clean(k, v)
            if nv:
                new.append([k, nv])
        with open(fp, 'w', encoding='utf-8') as fh:
            json.dump(new, fh, ensure_ascii=False, separators=(',', ':'))
        mb = os.path.getsize(fp) / 1048576
        print(f'{d["name"]:16s} {len(arr):7d} -> {len(new):7d}  {mb:7.2f}M')
        d['count'] = len(new)
        d['size'] = round(mb, 2)
    with open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)
    print('total', sum(d['count'] for d in idx))


if __name__ == '__main__':
    main()
