# -*- coding: utf-8 -*-
"""
批量导出 E:/mdx词库/*.mdx 为 学习工具平台/tools/dict/data/*.json
用法: PYTHONPATH=/tmp/stub python build_dict.py
"""
import os, sys, json, re, html, io

sys.path.insert(0, '/tmp/stub')
from readmdict import MDX

SRC = r'E:/mdx词库'
OUT = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/tools/dict/data'

# 显示名 / 词典代号（用于前端分组）
META = {
    '中医大辞典': ('中医大辞典', 'zycd', '中医'),
    '中国针灸学辞典': ('中国针灸学辞典', 'zjcd', '针灸'),
    '中医证病名大辞典': ('中医证病名大辞典', 'zbcd', '中医'),
    '中医名词术语选释': ('中医名词术语选释', 'mccd', '中医'),
    '本草綱目': ('本草纲目', 'bcgm', '本草'),
    '說文解字': ('说文解字', 'swjz', '文字'),
    '康熙字典': ('康熙字典', 'kangxi', '文字'),
    '[汉-汉] 古汉语常用字字典（修正版）': ('古汉语常用字字典', 'ghy', '文字'),
    'TCM_Textbook': ('中医教材仓库', 'tcmtb', '教材'),
}

TAG_RE = re.compile(r'<[^>]+>')
WS_RE = re.compile(r'[ \t\u00a0]+')


def to_text(hs: str) -> str:
    """MDX HTML -> 纯文本，尽力保留可读结构"""
    if not hs:
        return ''
    s = hs
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', '', s)
    s = re.sub(r'(?i)<br\s*/?>', '\n', s)
    s = re.sub(r'(?i)</(p|div|tr|li|h[1-6]|blockquote)>', '\n', s)
    s = re.sub(r'(?i)<(p|div|tr|li|h[1-6]|blockquote)\b[^>]*>', '\n', s)
    s = re.sub(r'(?i)</t[dh]>', '  ', s)
    s = re.sub(r'(?i)<img[^>]*?(?:alt|title)="([^"]*)"[^>]*>', r'\1', s)
    s = re.sub(r'(?i)<img[^>]*>', '', s)
    s = TAG_RE.sub('', s)
    s = html.unescape(s)
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = s.replace('\u3000', '　')
    lines = [WS_RE.sub(' ', ln).strip() for ln in s.split('\n')]
    out = []
    blank = 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append('')
    return '\n'.join(out).strip()


def clean_key(k: str) -> str:
    """去掉键里的内联图片/标签，取纯词头"""
    if not k:
        return ''
    s = re.sub(r'(?i)<img[^>]*>', '', k)
    s = TAG_RE.sub('', s)
    s = html.unescape(s).strip()
    return s


def main():
    os.makedirs(OUT, exist_ok=True)
    index = []
    total_all = 0
    for f in sorted(os.listdir(SRC)):
        if not f.lower().endswith('.mdx'):
            continue
        stem = f[:-4]
        name, code, group = META.get(stem, (stem, re.sub(r'\W+', '', stem)[:8] or 'x', '其他'))
        path = os.path.join(SRC, f)
        print(f'>> {f} ...', flush=True)
        try:
            mdx = MDX(path)
            entries = {}
            raw_n = 0
            for k, v in mdx.items():
                raw_n += 1
                kk = clean_key(k.decode('utf-8', 'ignore'))
                if not kk:
                    continue
                txt = to_text(v.decode('utf-8', 'ignore'))
                if not txt:
                    continue
                # 同名词条合并
                if kk in entries:
                    if txt not in entries[kk]:
                        entries[kk] = entries[kk] + '\n\n' + txt
                else:
                    entries[kk] = txt
            # 输出为 [[词头, 释义], ...]
            arr = [[k, v] for k, v in entries.items()]
            total_all += len(arr)
            fp = os.path.join(OUT, code + '.json')
            with open(fp, 'w', encoding='utf-8') as fh:
                json.dump(arr, fh, ensure_ascii=False, separators=(',', ':'))
            size = os.path.getsize(fp) / 1048576
            print(f'   raw={raw_n}  unique={len(arr)}  json={size:.2f}M', flush=True)
            index.append({'code': code, 'name': name, 'group': group,
                          'count': len(arr), 'file': code + '.json',
                          'size': round(size, 2)})
        except Exception as e:
            print(f'   FAIL {type(e).__name__}: {e}', flush=True)

    with open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    print(f'\nDONE  dicts={len(index)}  entries={total_all}')


if __name__ == '__main__':
    main()
