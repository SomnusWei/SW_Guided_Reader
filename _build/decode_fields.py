# -*- coding: utf-8 -*-
"""
解码 Mdict 风格 `NN` 字段标记（康熙字典 / 說文解字 / 本草綱目）
并处理 @@@LINK= 重定向。

标记语义（由样本反推）：
  50 词头   26 序号    54 异体字(空格分隔)   53 异体(空格分隔)  2  块结束
  40 缩进   35 四角/字符码   1  分号
  60 部首   62 部首外笔画 63 总笔画
  80 注音(ㄅㄆㄇ)  81 拼音  66 卷次
  101 页码  64 字序  65 反切序号  102 页码2
  42 释义区开始  13 释义项开始  14 又项开始  17/18 又项
  21 【小标题】  23 出处  22/6 项目
  95/4 说文条目正文  96 正文结束  9 注家  20 版本(大徐本/小徐本/段注本)
  28 反切  27 音切  67 卷次  87/130/132 部次
  30/31 参见词条  44 参见括号  43 相关
"""
import os, json, re

OUT = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/tools/dict/data'
RE_KW = re.compile(r'`(\d{1,3})`')


def _split(text):
    """切成 [(code, payload)]，payload 为到下一个标记之间的文本"""
    parts = []
    pos = 0
    for m in RE_KW.finditer(text):
        if m.start() > pos:
            parts.append((None, text[pos:m.start()]))
        parts.append((m.group(1), ''))
        pos = m.end()
    if pos < len(text):
        parts.append((None, text[pos:]))
    return parts


def decode_kangxi(k, text):
    """康熙字典 -> 结构化文本"""
    out = []
    radical = stroke_out = stroke_all = ''
    zhuyin = pinyin = ''
    volume = ''
    pages = []
    body_buf = []
    gloss_text = []

    parts = _split(text)
    i = 0
    n = len(parts)
    def nxt(j):
        # 取 code 之后紧跟的文本
        if j + 1 < n and parts[j + 1][0] is None:
            return parts[j + 1][1].strip(), j + 2
        return '', j + 1

    variants = ''
    while i < n:
        c, t = parts[i]
        if c is None:
            i += 1; continue
        if c == '54' or c == '53':
            v, i = nxt(i)
            variants = (variants + ' ' + v).strip()
        elif c == '60':
            radical, i = nxt(i)
        elif c == '62':
            stroke_out, i = nxt(i)
        elif c == '63':
            stroke_all, i = nxt(i)
        elif c == '80':
            zhuyin, i = nxt(i)
        elif c == '81':
            pinyin, i = nxt(i)
        elif c == '66':
            volume, i = nxt(i)
        elif c in ('101', '102'):
            v, i = nxt(i)
            if v.isdigit():
                pages.append(v)
        elif c == '42':
            i += 1
            # 释义区：收集到块结束
            buf = []
            while i < n:
                cc, tt = parts[i]
                if cc is None:
                    buf.append(tt); i += 1; continue
                if cc in ('2',):
                    i += 1
                    continue
                if cc in ('13', '14', '17', '18'):
                    buf.append('\n')
                    i += 1
                    continue
                if cc in ('20', '27', '28', '43', '44', '45', '46'):
                    i += 1
                    continue
                i += 1
            gloss_text.append(''.join(buf))
        else:
            i += 1

    # 头信息
    head = []
    if radical:
        s = f'部首：{radical}'
        if stroke_out or stroke_all:
            s += f'　部外笔画：{stroke_out or "-"}　总笔画：{stroke_all or "-"}'
        head.append(s)
    if variants:
        head.append('异体：' + '　'.join(variants.split()))
    if zhuyin or pinyin:
        s = ''
        if zhuyin:
            s += f'注音：{zhuyin}'
        if pinyin:
            s += ('　' if s else '') + f'拼音：{pinyin}'
        head.append(s)
    if volume:
        s = f'出处：{volume}'
        if pages:
            s += f'　页 {"/".join(pages)}'
        head.append(s)

    gloss = '\n'.join(gloss_text)
    gloss = re.sub(r'\n{2,}', '\n', gloss).strip()
    gloss = gloss.replace('`', '')

    out = '\n'.join(head)
    if gloss:
        out += ('\n\n' if out else '') + gloss
    return out


def decode_shuowen(k, text):
    """說文解字 -> 按 大徐本 / 小徐本 / 段注本 分节"""
    variants = ''
    zhuyin = pinyin = ''
    fanqie = ''
    juan = ''
    sections = []      # [(版本名, [正文行])]
    cur_name = None
    cur_lines = []
    cur_note_name = None

    parts = _split(text)
    n = len(parts)
    def nxt(j):
        if j + 1 < n and parts[j + 1][0] is None:
            return parts[j + 1][1].strip(), j + 2
        return '', j + 1

    def flush():
        nonlocal cur_lines
        if cur_name and cur_lines:
            sections.append((cur_name, cur_lines[:]))
        cur_lines = []

    i = 0
    while i < n:
        c, t = parts[i]
        if c is None:
            i += 1; continue
        if c in ('54', '53'):
            v, i = nxt(i)
            variants = (variants + v + ' ').strip()
        elif c == '80':
            zhuyin, i = nxt(i)
        elif c == '81':
            pinyin, i = nxt(i)
        elif c == '20':
            # 新版本开始
            flush()
            cur_name, i = nxt(i)
            cur_lines = []
        elif c in ('28', '27'):
            v, i = nxt(i)
            if v:
                cur_lines.append(f'〔{v}〕')
        elif c == '67':
            v, i = nxt(i)
            if v:
                cur_lines.append(f'（{v}）')
        elif c == '95':
            # 正文开始，跳过其后的编号
            j = i + 1
            if j < n and parts[j][0] is None:
                j += 1
            # 编号紧跟（如 C00004）
            if j < n and parts[j][0] is None and parts[j][1].strip() and len(parts[j][1].strip()) < 12:
                j += 1
            if j < n and parts[j][0] is None:
                txt = parts[j][1].strip()
                if txt:
                    cur_lines.append(txt)
                j += 1
            i = j
        elif c == '96':
            i += 1
        elif c == '9' or c == '13':
            nm, i = nxt(i)
            if i < n:
                cc, _ = parts[i]
                if cc is None:
                    txt = parts[i][1].strip(); i += 1
                    if txt:
                        cur_lines.append(f'〔{nm}〕{txt}')
                elif nm:
                    cur_lines.append(f'〔{nm}〕')
        else:
            i += 1
    flush()

    head = []
    if variants:
        head.append('异体：' + '　'.join(variants.split()))
    if zhuyin or pinyin:
        s = ''
        if zhuyin:
            s += '注音：' + zhuyin
        if pinyin:
            s += ('　' if s else '') + '拼音：' + pinyin
        head.append(s)

    body = []
    for name, lines in sections:
        body.append('【' + name + '】')
        body.extend(lines)
        body.append('')
    out = '\n'.join(head)
    b = '\n'.join(body).strip()
    b = re.sub(r'\n{3,}', '\n\n', b).replace('`', '')
    if b:
        out += ('\n\n' if out else '') + b
    return out


def decode_bencao(k, text):
    """本草綱目 -> 标题 + 卷次 + 分项"""
    title = ''
    volume = ''
    alias = ''
    out = []

    parts = _split(text)
    n = len(parts)
    buf = []
    def nxt(j):
        if j + 1 < n and parts[j + 1][0] is None:
            return parts[j + 1][1].strip(), j + 2
        return '', j + 1

    i = 0
    while i < n:
        c, t = parts[i]
        if c is None:
            buf.append(t); i += 1; continue
        if c == '51':
            title, i = nxt(i)
        elif c == '53':
            alias, i = nxt(i)
        elif c == '66':
            volume, i = nxt(i)
        elif c == '21':
            v, i = nxt(i)
            buf.append('\n【' + v + '】\n')
        elif c == '23':
            v, i = nxt(i)
            buf.append('〔出处〕' + v)
        elif c in ('4', '22'):
            buf.append('\n')
            i += 1
        elif c == '6':
            buf.append('\n')
            i += 1
        elif c == '1':
            i += 1
        elif c == '2':
            buf.append('\n')
            i += 1
        elif c in ('30', '31'):
            v, i = nxt(i)
            if v:
                buf.append('〔参见〕' + v)
        elif c == '44':
            i += 1
        else:
            i += 1

    head = []
    if volume:
        head.append('出处：' + volume)
    if alias and alias != title:
        head.append('别名：' + alias)
    body = ''.join(buf).replace('`', '')
    body = re.sub(r'[ \t\u3000]+', ' ', body)
    body = re.sub(r'\n[ ]+', '\n', body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    out = '\n'.join(head)
    if body:
        out += ('\n\n' if out else '') + body
    return out


DECODERS = {
    'kangxi': decode_kangxi,
    'swjz': decode_shuowen,
    'bcgm': decode_bencao,
}


def main():
    for code, fn in DECODERS.items():
        fp = os.path.join(OUT, code + '.json')
        arr = json.load(open(fp, encoding='utf-8'))
        new = []
        links = {}
        for k, v in arr:
            if v.startswith('@@@LINK='):
                links[k] = v.split('=', 1)[1].strip()
                continue
            try:
                nv = fn(k, v)
            except Exception as e:
                nv = v
            if nv and nv.strip():
                new.append([k, nv])
        # 解析链接
        d = dict(new)
        resolved = 0
        for k, tgt in links.items():
            if tgt in d and k not in d:
                new.append([k, d[tgt]]); resolved += 1
        with open(fp, 'w', encoding='utf-8') as fh:
            json.dump(new, fh, ensure_ascii=False, separators=(',', ':'))
        mb = os.path.getsize(fp) / 1048576
        print(f'{code:8s} {len(arr):6d} -> {len(new):6d}  link={len(links)}(ok {resolved})  {mb:.2f}M')


if __name__ == '__main__':
    main()
