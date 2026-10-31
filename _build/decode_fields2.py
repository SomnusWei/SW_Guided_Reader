# -*- coding: utf-8 -*-
"""
通用 Mdict `NN` 字段标记解码（保守版）
策略：不猜测字段语义，只做「去标记 + 智能分隔」，保证内容一字不丢。
特殊增强：康熙字典的头部信息（部首/笔画/注音/卷次）单独提取。
重跑前会从 data_backup_before_decode 读取原始数据。
"""
import os, json, re, sys

OUT = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/tools/dict/data'
BAK = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/_build/data_backup_before_decode'

RE_KW = re.compile(r'`(\d{1,3})`')

# 这些标记是"分隔/换行"性质：输出换行
BREAK = {'2', '13', '14', '17', '18', '4', '6', '22', '9', '20', '21', '46', '43', '44', '54', '53'}


def strip_marks(text, head_codes=None):
    """通用：返回 [(code, payload)] 序列，供后续拼接"""
    parts = []
    pos = 0
    for m in RE_KW.finditer(text):
        if m.start() > pos:
            parts.append((None, text[pos:m.start()]))
        parts.append((m.group(1), None))
        pos = m.end()
    if pos < len(text):
        parts.append((None, text[pos:]))
    return parts


def polish(s, key=''):
    """去掉混入的编号串、纯数字行、行首重复词头，收拾空白"""
    s = s.replace('`', '')
    # 连续 3 位以上数字串 = 排版编号，删除
    s = re.sub(r'\d{5,}', '', s)
    s = re.sub(r'[ \t\u3000]{2,}', '　', s)
    s = re.sub(r'\n[ \t\u3000]+', '\n', s)
    s = re.sub(r'[ \t\u3000]+\n', '\n', s)
    # 纯数字 / 纯编号行整行删除
    lines = []
    for ln in s.split('\n'):
        t = ln.strip()
        if t and re.fullmatch(r'[\dA-Za-z]{3,20}', t) and any(c.isdigit() for c in t):
            continue
        lines.append(ln)
    # 行首重复出现的词头（允许多次）去掉
    if key:
        while lines and lines[0].strip() == key:
            lines.pop(0)
        # 首行若为 "词头+卷次信息"，拆掉重复的词头前缀
        if lines and lines[0].startswith(key) and len(lines[0]) > len(key):
            rest = lines[0][len(key):]
            if rest:
                lines[0] = rest.lstrip('　 ').lstrip(key)
                if not lines[0].strip():
                    lines.pop(0)
    s = '\n'.join(lines)
    # 〔音〕〔他前切〕 -> 〔他前切〕
    s = re.sub(r'〔([^〕]{1,6})〕〔\1〕', r'〔\1〕', s)
    s = re.sub(r'〔音〕〔', '〔', s)
    # 纯编号残行（如 GTSC / C0004 / 12 位以内字母数字）
    s = '\n'.join(x for x in s.split('\n')
                  if not (x.strip() and re.fullmatch(r'[A-Za-z0-9]{1,10}', x.strip())))
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def generic_decode(text):
    """去标记，保留全部文字；块结束处换行"""
    parts = strip_marks(text)
    out = []
    for code, payload in parts:
        if code is None:
            out.append(payload)
        elif code in BREAK:
            out.append('\n')
        # 其余标记：直接丢弃，但若前后都需要分隔，靠空白自然分开
    s = ''.join(out)
    s = s.replace('`', '')
    # 收拾空白
    s = re.sub(r'[ \t\u3000]{2,}', '　', s)
    s = re.sub(r'\n[ \t\u3000]+', '\n', s)
    s = re.sub(r'[ \t\u3000]+\n', '\n', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return polish(s)


def kangxi_decode(text):
    """康熙：提取头部字段，正文区 42 之后按 13/14 换行，21 做【标题】"""
    parts = strip_marks(text)
    n = len(parts)
    head = {}
    body = []

    def take(j):
        if j + 1 < n and parts[j + 1][0] is None:
            return parts[j + 1][1].strip(), j + 2
        return '', j + 1

    i = 0
    while i < n:
        code, _ = parts[i]
        if code is None:
            i += 1
            continue
        if code in ('54', '53'):
            v, i = take(i)
            head.setdefault('variants', []).append(v)
        elif code == '60':
            head['radical'], i = take(i)
        elif code == '62':
            head['stroke_out'], i = take(i)
        elif code == '63':
            head['stroke_all'], i = take(i)
        elif code == '80':
            head['zhuyin'], i = take(i)
        elif code == '81':
            head['pinyin'], i = take(i)
        elif code == '66':
            head['volume'], i = take(i)
        elif code in ('101', '102'):
            v, i = take(i)
            v = re.sub(r'\D', '', v)
            if v:
                head.setdefault('pages', []).append(v)
        elif code == '42':
            i += 1
            # 释义区
            while i < n:
                cc, _ = parts[i]
                if cc is None:
                    body.append(parts[i][1]); i += 1; continue
                if cc in ('2',):
                    i += 1; continue
                if cc in ('13', '14', '17', '18'):
                    body.append('\n'); i += 1; continue
                if cc == '21':
                    v, i = take(i)
                    body.append('\n【' + v + '】')
                    continue
                i += 1
        else:
            i += 1

    lines = []
    if head.get('radical'):
        s = f"部首：{head['radical']}"
        if head.get('stroke_out') or head.get('stroke_all'):
            s += f"　部外笔画：{head.get('stroke_out') or '-'}　总笔画：{head.get('stroke_all') or '-'}"
        lines.append(s)
    vs = [v for v in head.get('variants', []) if v]
    if vs:
        allv = '　'.join(dict.fromkeys(w for v in vs for w in v.split()))
        # 去掉与词头相同的
        lines.append('异体：' + allv)
    if head.get('zhuyin') or head.get('pinyin'):
        s = ''
        if head.get('zhuyin'):
            s += '注音：' + head['zhuyin']
        if head.get('pinyin'):
            s += ('　' if s else '') + '拼音：' + head['pinyin']
        lines.append(s)
    if head.get('volume'):
        s = '出处：' + head['volume']
        pgs = [x for x in head.get('pages', []) if x]
        if pgs:
            s += '　页 ' + '/'.join(dict.fromkeys(pgs))
        lines.append(s)

    g = ''.join(body).replace('`', '')
    g = re.sub(r'[ \t\u3000]+', '', g)
    g = re.sub(r'\n{2,}', '\n', g).strip()

    out = '\n'.join(lines)
    if g:
        out += ('\n\n' if out else '') + g
    return out



def shuowen_decode(text):
    """說文解字：按 大徐本/小徐本/段注本 分节；块 2 换行，1 行内分隔"""
    parts = strip_marks(text)
    n = len(parts)
    variants, zhuyin, pinyin = '', '', ''
    cur_name = None
    cur_tag = ''        # 当前小标题（正文 / 鍇注 / 段注 / 附注 …）
    cur_text = []
    sections = []

    def flush():
        nonlocal cur_text, cur_tag
        if cur_name is not None:
            segments = []
            for tag, txt in cur_text:
                txt = txt.strip()
                if not txt:
                    continue
                if tag == '正文':
                    segments.append(txt)
                elif tag == '杂':
                    pass
                else:
                    segments.append('〔' + tag + '〕' + txt)
            if segments:
                sections.append((cur_name, segments))
        cur_text = []

    def push(txt, tag=None):
        t = (tag or cur_tag or '正文')
        if cur_text and cur_text[-1][0] == t:
            cur_text[-1][1] = cur_text[-1][1] + txt
        else:
            cur_text.append([t, txt])

    def take(j):
        if j + 1 < n and parts[j + 1][0] is None:
            return parts[j + 1][1].strip(), j + 2
        return '', j + 1

    i = 0
    while i < n:
        code, _ = parts[i]
        if code is None:
            txt = parts[i][1]
            if txt and cur_text is not None:
                push(txt)
            i += 1
            continue
        if code in ('54', '53'):
            v, i = take(i)
            variants = (variants + v).strip()
        elif code == '80':
            zhuyin, i = take(i)
        elif code == '81':
            pinyin, i = take(i)
        elif code in ('14', '20'):
            nm, i2 = take(i)
            # 若取到的是版本名（大徐本/小徐本/段注本），开新节
            if nm and '本' in nm:
                flush()
                cur_name = nm
                cur_tag = ''
                i = i2
            else:
                i += 1
        elif code == '4':
            # 新的一段：清掉当前 tag，回到正文
            cur_tag = ''
            i += 1
        elif code == '95':
            # 条目正文：跳过 [8B/字母数字混合编号]
            j = i + 1
            if j < n and parts[j][0] is None:
                j += 1
            if j < n and parts[j][0] is None:
                v = parts[j][1].strip()
                if v and not re.fullmatch(r'[A-Za-z0-9]{2,10}', v):
                    push(v, '正文')
                j += 1
            i = j
        elif code == '96':
            i += 1
        elif code in ('9', '13'):
            v, i = take(i)
            cur_tag = v or '注'
            # 紧跟的正文
            if i < n and parts[i][0] is None:
                t = parts[i][1].strip()
                if t:
                    push(t, cur_tag)
                    i += 1
        elif code == '2':
            i += 1
        elif code == '1':
            # 行内分隔，转为一个空格
            push(' ')
            i += 1
        elif code in ('28', '27'):
            v, i = take(i)
            if v:
                v = v.strip('〔〕')
                push('〔' + v + '〕', '音')
        elif code == '67':
            v, i = take(i)
            if v:
                push('（' + v + '）', '卷')
        else:
            v, i2 = take(i)
            if v and re.fullmatch(r'[A-Za-z0-9\u3000 ]{1,12}', v):
                i = i2          # 编号类载荷，丢弃
            elif v and v not in ('',):
                push(v, '杂')
                i = i2
            else:
                i += 1
    flush()

    head = []
    if variants:
        head.append('异体：' + '　'.join(dict.fromkeys(variants)))
    if zhuyin or pinyin:
        t = ''
        if zhuyin:
            t += '注音：' + zhuyin
        if pinyin:
            t += ('　' if t else '') + '拼音：' + pinyin
        head.append(t)

    body = []
    for name, segs in sections:
        body.append('【' + name + '】')
        body.extend(segs)
        body.append('')
    b = '\n'.join(body)
    b = re.sub(r'[ \t\u3000]{2,}', '　', b)
    b = re.sub(r'\n{3,}', '\n\n', b).strip()
    out = '\n'.join(head)
    if b:
        out += ('\n\n' if out else '') + b
    return out


def main():
    # 从备份取原始
    src_dir = BAK if os.path.isdir(BAK) else OUT
    for code in ('kangxi', 'swjz', 'bcgm'):
        fp_src = os.path.join(src_dir, code + '.json')
        arr = json.load(open(fp_src, encoding='utf-8'))
        new, links = [], {}
        for k, v in arr:
            if v.startswith('@@@LINK='):
                links[k] = v.split('=', 1)[1].strip()
                continue
            if code == 'kangxi':
                nv = polish(kangxi_decode(v), k)
            elif code == 'swjz':
                nv = polish(shuowen_decode(v), k)
            else:
                nv = polish(generic_decode(v), k)
            if nv.strip():
                new.append([k, nv])
        d = {k: v for k, v in new}
        ok = 0
        for k, tgt in links.items():
            if tgt in d and k not in d:
                new.append([k, d[tgt]]); ok += 1
        fp = os.path.join(OUT, code + '.json')
        with open(fp, 'w', encoding='utf-8') as fh:
            json.dump(new, fh, ensure_ascii=False, separators=(',', ':'))
        print(f'{code:8s} {len(arr):6d} -> {len(new):6d}  link={len(links)}(ok {ok})  {os.path.getsize(fp)/1048576:.2f}M')


if __name__ == '__main__':
    main()
