#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
讲义 HTML → OneNote 导入适配版 转换器（yixue-zonghe 技能 · 功能二 讲义制作）

把 class 驱动的网页版讲义，转换为 OneNote 导入优化版：
  · 零 class，全部内联 style，字号一律 pt
  · 固定侧栏目录      → 文内目录表格
  · 渐变 / 圆角封面   → bgcolor 单元格表格
  · 卡片（border-left）→ 双格表格（6px 色条 + 内容格）+ 嵌套表头小表
  · 引文（CSS ::before/::after 生成「」）→ 白底虚线表格，括号改为实体字符
  · code / 时间戳（.ts）→ Consolas 内联高亮 span
  · 数据表            → border="1" + th/td 内联样式 + 隔行底色
  · 移除动画 / 浮动 / 渐变 / CSS 变量；补 @page A4 打印设定

用法：
    python to_onenote.py --src 讲义.html --out 讲义_OneNote版.html
    python to_onenote.py --src 讲义.html --out 讲义_OneNote版.html --primary "#1e4d8c"
    python to_onenote.py --src 讲义.html --out 讲义_OneNote版.html --check

主题色只需给 --primary，其余（深色、浅色底纹、卡片描边）自动派生。
"""
import argparse
import os
import re
import sys
from html.parser import HTMLParser

VOID = {'meta', 'br', 'hr', 'img', 'link', 'input', 'area', 'base',
        'col', 'embed', 'source', 'track', 'wbr'}


# ---------------------------------------------------------------- 颜色工具
def _hex2rgb(h):
    h = h.strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb2hex(t):
    return '#%02x%02x%02x' % tuple(max(0, min(255, int(round(v)))) for v in t)


def darken(h, k=0.70):
    return _rgb2hex(tuple(c * k for c in _hex2rgb(h)))


def tint(h, k=0.04):
    """向白色混合：k 越小越接近白色"""
    return _rgb2hex(tuple(255 - (255 - c) * k for c in _hex2rgb(h)))


# ---------------------------------------------------------------- 主题
class Theme:
    def __init__(self, primary='#0f6b6b', primary_dark=None, accent='#b8860b',
                 card_bg=None, card_bd=None, flow_bg=None, flow_bd=None):
        self.P = primary
        self.PD = primary_dark or darken(primary, 0.70)
        self.ACCENT = accent
        self.CARD_BG = card_bg or tint(primary, 0.04)
        self.CARD_BD = card_bd or tint(primary, 0.22)
        self.FLOW_BG = flow_bg or tint(primary, 0.06)
        self.FLOW_BD = flow_bd or tint(primary, 0.20)
        self.SUM_BG = tint(primary, 0.10)
        # 语义卡片固定配色（与主题无关，全库统一）
        self.LIU, self.LIU_BG, self.LIU_BD = '#7a4b00', '#fff8ec', '#e8b96a'
        self.LIU_BAR = '#b8860b'
        self.MN, self.MN_BG, self.MN_BD, self.MN_T = '#6b4fbb', '#f3f0fb', '#cfc4ee', '#4b338e'
        self.MN_BIG, self.MN_BIG_BD = '#3b2675', '#d8cff0'
        self.BND, self.BND_BG, self.BND_BD = '#a83232', '#fdf2f2', '#e7bcbc'
        self.CLN, self.CLN_BG, self.CLN_BD = '#1a6b3c', '#eef8f1', '#bfe0cc'
        self.NT, self.NT_BG, self.NT_BD, self.NT_T = '#2a6099', '#eef4fb', '#c4d8ee', '#1f4a76'

    def card_palette(self):
        return {
            'card-textbook': (self.P, self.CARD_BG, self.CARD_BD, self.PD),
            'card-liu': (self.LIU_BAR, self.LIU_BG, self.LIU_BD, self.LIU),
            'card-mnemonic': (self.MN, self.MN_BG, self.MN_BD, self.MN_T),
            'card-boundary': (self.BND, self.BND_BG, self.BND_BD, self.BND),
            'card-clinic': (self.CLN, self.CLN_BG, self.CLN_BD, self.CLN),
            'card-note': (self.NT, self.NT_BG, self.NT_BD, self.NT_T),
        }


TS_STYLE = ('font-family:Consolas,monospace;font-size:9pt;background-color:#f6e7cd;'
            'color:#7a4b00;font-weight:bold;white-space:nowrap;')


# ---------------------------------------------------------------- 极简 DOM
class Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent')

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.parent = parent

    @property
    def cls(self):
        return self.attrs.get('class', '') or ''

    def has(self, *cs):
        parts = self.cls.split()
        return any(c in parts for c in cs)

    def find_child(self, *cs):
        for ch in self.children:
            if ch.tag != '#text' and ch.has(*cs):
                return ch
        return None

    def text_of_children(self):
        out = []
        for c in self.children:
            if c.tag == '#text':
                out.append(c.attrs.get('data', ''))
        return ''.join(out)


class Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = Node('root')
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID:
            self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID:
            return
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root and node.parent is not None:
            self.cur = node.parent

    def _txt(self, data):
        if data:
            t = Node('#text')
            t.attrs['data'] = data
            self.cur.children.append(t)

    def handle_data(self, data):
        self._txt(data)

    def handle_entityref(self, name):
        self._txt(f'&{name};')

    def handle_charref(self, name):
        self._txt(f'&#{name};')


# ---------------------------------------------------------------- 转换器
class Converter:
    def __init__(self, theme, toc_meta='', note=''):
        self.T = theme
        self.toc_meta = toc_meta
        self.note = note
        self.stats = {}
        self.toc_items = []
        self.toc_tiers = []       # [('grp'|'l1'|'l2', text), ...]
        self.toc_title = '目 录'
        self.toc_meta_locked = False

    def bump(self, k, n=1):
        self.stats[k] = self.stats.get(k, 0) + n

    # ---- 基础 ----
    def text(self, n):
        d = n.attrs.get('data', '')
        return (d.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def kids(self, n):
        return ''.join(self.node(c) for c in n.children)

    def kids_except(self, n, exclude):
        return ''.join(self.node(c) for c in n.children if c not in exclude)

    # ---- 样式清洗 ----
    def clean_style(self, s):
        if not s:
            return ''
        out = []
        for decl in s.split(';'):
            decl = decl.strip()
            if not decl or ':' not in decl:
                continue
            k, v = decl.split(':', 1)
            k, v = k.strip().lower(), v.strip()
            if k in ('border-radius', 'box-shadow', 'background-image', 'transition',
                     'background', 'background-color', 'position', 'overflow', 'float',
                     'width', 'max-width', 'min-width'):
                continue
            t = self.T
            for a, b in (('var(--primary-dark)', t.PD), ('var(--primary-light)', t.CARD_BG),
                         ('var(--primary)', t.P), ('var(--ink)', '#1b2430'),
                         ('var(--ink-soft)', '#4a5764')):
                v = v.replace(a, b)
            if k == 'font-size' and 'px' in v:
                v = re.sub(r'(-?\d+(?:\.\d+)?)px',
                           lambda m: '%gpt' % round(float(m.group(1)) * 0.75, 1), v)
            out.append(f'{k}:{v}')
        return ';'.join(out)

    def inline(self, s):
        c = self.clean_style(s)
        return f' style="{c}"' if c else ''

    # ---- 分派 ----
    def node(self, n):
        if n.tag == '#text':
            return self.text(n)
        t = n.tag
        if t in ('head', 'style', 'script', 'title', 'meta', 'link'):
            return ''
        if t in ('div', 'aside', 'main', 'section', 'article', 'header', 'footer'):
            return self.block(n)
        if t == 'h1':
            return self.kids(n)
        if t == 'h2':
            return self.h2(n)
        if t in ('h3', 'h4'):
            return self.h34(n, int(t[1]))
        if t == 'p':
            return self.p(n)
        if t == 'table':
            return self.table(n)
        if t in ('ul', 'ol'):
            return f'<{t} style="margin:6px 0 6px 0;padding-left:22px;">{self.kids(n)}</{t}>'
        if t == 'li':
            return f'<li style="margin:3px 0;">{self.kids(n)}</li>'
        if t in ('strong', 'b'):
            return f'<strong style="color:#0c2038;">{self.kids(n)}</strong>'
        if t in ('em', 'i'):
            return f'<em>{self.kids(n)}</em>'
        if t == 'code':
            return ('<span style="font-family:Consolas,monospace;font-size:9.5pt;'
                    f'background-color:#eef3f4;color:#0b4a4a;">{self.kids(n)}</span>')
        if t == 'br':
            return '<br>'
        if t == 'img':
            return self.img_tag(n)
        if t == 'span':
            return self.span(n)
        if t == 'a':
            return self.kids(n)
        if t in ('small',):
            return self.kids(n)
        if t in ('sub', 'sup'):
            return f'<{t} style="font-size:80%;">{self.kids(n)}</{t}>'
        return self.kids(n)

    def img_tag(self, n):
        """<img> 直通：保留 src/alt/width，可选 data-caption 生成图注。

        原版 node() 无 img 分支，图片会被静默丢弃；此处补齐，供"配图"讲义使用。
        """
        src = n.attrs.get('src', '')
        alt = n.attrs.get('alt', '')
        w = n.attrs.get('width', '')
        cap = n.attrs.get('data-caption', '')
        self.bump('img')
        parts = [f'src="{src}"', f'alt="{alt}"']
        if w:
            parts.append(f'width="{w}"')
        img = '<img ' + ' '.join(parts) + ' style="max-width:100%;height:auto;">'
        if not cap:
            return img
        return ('<table width="100%" cellspacing="0" cellpadding="0" '
                'style="border-collapse:collapse;width:100%;margin:8px 0;"><tr>'
                '<td bgcolor="#ffffff" style="background-color:#ffffff;'
                'border:1px solid #dde5e8;padding:8px 10px;text-align:center;">'
                + img
                + '<p style="margin:6px 0 0 0;color:#7a8b96;font-size:9pt;">'
                + cap + '</p></td></tr></table>')

    def span(self, n):
        if n.has('ts'):
            return f'<span style="{TS_STYLE}">{self.kids(n)}</span>'
        if n.has('badge', 'num'):
            return self.kids(n)
        return f'<span{self.inline(n.attrs.get("style", ""))}>{self.kids(n)}</span>'

    def block(self, n):
        if n.attrs.get('_skip'):
            return ''
        if n.has('side'):
            self.collect_sidebar(n)
            return ''
        if n.has('cover'):
            return self.cover(n)
        if n.has('hd'):
            return ''
        if n.has('card'):
            return self.card(n)
        if n.has('q'):
            return self.quote(n)
        if n.has('big'):
            return self.big(n)
        if n.has('exam-grid'):
            return self.exam(n)
        if n.has('flow'):
            return self.flow_html(self.kids(n))
        if n.has('summary-box'):
            return self.summary(n)
        if n.has('footer'):
            txt = re.sub(r'[ \t\r\n]+', ' ', self.kids(n)).strip()
            self.bump('footer')
            return ('<p style="margin:22px 0 10px 0;text-align:center;color:#9aa8b1;'
                    f'font-size:9.5pt;letter-spacing:1px;">{txt}</p>')
        if n.has('exam-hot', 'exam-cold'):
            return self.kids(n)
        if n.has('kicker', 'rule', 'src', 'tagrow'):
            return ''
        return self.kids(n)

    # ---- 侧栏目录采集 ----
    def collect_sidebar(self, side):
        for p in side.children:
            if p.tag == '#text':
                continue
            if p.tag in ('p', 'div') and p.has('meta'):
                if not self.toc_meta_locked:
                    self.toc_meta = re.sub(r'[ \t\r\n]+', ' ', self.kids(p)).strip()
            if p.tag == 'h2':
                self.toc_title = self.kids(p).strip() or self.toc_title
            if p.tag == 'ol':
                for li in p.children:
                    if li.tag != 'li':
                        continue
                    a = li.find_child('a') or li
                    txt = re.sub(r'[ \t\r\n]+', ' ', self.kids(a)).strip()
                    if not txt:
                        continue
                    if li.has('grp'):
                        tier = 'grp'
                    elif li.has('l2'):
                        tier = 'l2'
                    else:
                        tier = 'l1'
                    self.toc_tiers.append((tier, txt))
                    if tier == 'l1':
                        self.toc_items.append(txt)

    # ---- 标题 ----
    def h2(self, n):
        if not n.has('sec'):
            return ''
        num, body = '', []
        for c in n.children:
            if c.tag != '#text' and c.has('num'):
                num = re.sub(r'[ \t\r\n]+', ' ', c.text_of_children()).strip()
            else:
                body.append(self.node(c))
        title = re.sub(r'[ \t\r\n]+', ' ', ''.join(body)).strip()
        head = (f'<strong style="color:{self.T.ACCENT};font-size:11.5pt;">'
                f'［{num}］</strong>　') if num else ''
        rule = ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                f'width:100%;margin:0 0 10px 0;"><tr><td bgcolor="{self.T.P}" '
                f'style="background-color:{self.T.P};height:3px;line-height:3px;'
                'font-size:1pt;">&nbsp;</td></tr></table>')
        self.bump('h2')
        return (f'<h2 style="font-size:17pt;color:{self.T.PD};margin:22px 0 4px 0;'
                f'font-weight:bold;">{head}{title}</h2>{rule}')

    def h34(self, n, lvl):
        size = '13.5pt' if lvl == 3 else '11.5pt'
        color = self.T.PD if lvl == 3 else '#20303c'
        margin = '15px 0 4px 0' if lvl == 3 else '11px 0 4px 0'
        self.bump(f'h{lvl}')
        return (f'<h{lvl} style="font-size:{size};color:{color};margin:{margin};font-weight:bold;">'
                f'<span style="color:{self.T.P};">▍</span>{self.kids(n)}</h{lvl}>')

    # ---- 段落 ----
    def p(self, n):
        src = n.attrs.get('style', '')
        if 'background' in src:
            return self.flow_html(self.kids(n))
        parts = [x for x in self.clean_style(src).split(';') if x]
        if not any(x.startswith('margin') for x in parts):
            parts.insert(0, 'margin:6px 0')
        self.bump('p')
        return f'<p style="{";".join(parts)};">{self.kids(n)}</p>'

    def split_br(self, html):
        segs = [s.strip() for s in re.split(r'<br\s*/?>', html) if s.strip()]
        if len(segs) <= 1:
            return f'<p style="margin:6px 0;">{html}</p>'
        return ''.join(f'<p style="margin:6px 0;">{s}</p>' for s in segs)

    def flow_html(self, inner):
        self.bump('flow')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:9px 0;"><tr>'
                f'<td bgcolor="{self.T.FLOW_BG}" style="background-color:{self.T.FLOW_BG};'
                f'border:1px solid {self.T.FLOW_BD};padding:10px 12px;text-align:center;'
                f'font-weight:bold;color:{self.T.PD};line-height:1.9;">'
                f'{self.split_br(inner)}</td></tr></table>')

    # ---- 卡片 ----
    def card(self, n):
        accent, bg, bd, tcolor = self.T.P, self.T.CARD_BG, self.T.CARD_BD, self.T.PD
        for k, v in self.T.card_palette().items():
            if n.has(k):
                accent, bg, bd, tcolor = v
                break
        hd = n.find_child('hd')
        rest = self.kids_except(n, {hd} if hd else set())
        head_html = self.hd_table(hd, accent, tcolor) if hd else ''
        self.bump('card')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:9px 0;"><tr>'
                f'<td width="6" bgcolor="{accent}" style="background-color:{accent};width:6px;'
                'font-size:1pt;">&nbsp;</td>'
                f'<td bgcolor="{bg}" style="background-color:{bg};border:1px solid {bd};'
                f'padding:11px 15px;">{head_html}{rest}</td></tr></table>')

    def hd_table(self, hd, accent, tcolor):
        badge = next((c for c in hd.children if c.tag != '#text' and c.has('badge')), None)
        title_html = self.kids_except(hd, {badge} if badge else set()).strip()
        if badge is None:
            return ('<table cellspacing="0" cellpadding="0" '
                    'style="border-collapse:collapse;margin:0 0 8px 0;"><tr>'
                    f'<td style="font-size:10.5pt;font-weight:bold;color:{tcolor};">'
                    f'{title_html}</td></tr></table>')
        badge_text = re.sub(r'[ \t\r\n]+', ' ', badge.text_of_children()).strip()
        return ('<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'margin:0 0 8px 0;"><tr>'
                f'<td bgcolor="{accent}" style="background-color:{accent};color:#ffffff;'
                f'font-size:9.5pt;font-weight:bold;padding:2px 8px;white-space:nowrap;">'
                f'{badge_text}</td>'
                f'<td style="padding:0 0 0 8px;font-size:10.5pt;font-weight:bold;'
                f'color:{tcolor};">{title_html}</td></tr></table>')

    # ---- 引文 / 大字 ----
    def quote(self, n):
        self.bump('quote')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:7px 0;"><tr>'
                '<td bgcolor="#ffffff" style="background-color:#ffffff;'
                f'border:1px dashed {self.T.LIU_BD};padding:8px 12px;color:#4a3200;line-height:1.65;">'
                f'<p style="margin:0;">「{self.kids(n)}」</p></td></tr></table>')

    def big(self, n):
        self.bump('big')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:8px 0;"><tr>'
                '<td bgcolor="#ffffff" style="background-color:#ffffff;'
                f'border:1px solid {self.T.MN_BIG_BD};padding:9px 12px;text-align:center;'
                f'font-size:12.5pt;font-weight:bold;color:{self.T.MN_BIG};">'
                f'{self.split_br(self.kids(n))}</td></tr></table>')

    # ---- 考点边界双栏 ----
    def exam(self, n):
        cells = []
        for c in n.children:
            if c.tag == '#text':
                continue
            if c.has('exam-hot'):
                cells.append(('#fdeff0', '#f0c2c6', self.T.BND, c))
            elif c.has('exam-cold'):
                cells.append(('#f2f6f8', '#d5e0e6', '#4a5764', c))
        tds = []
        for bg, bd, tc, node in cells:
            body = []
            for ch in node.children:
                if ch.tag == '#text':
                    continue
                if ch.has('t'):
                    body.append(f'<p style="margin:0 0 6px 0;font-weight:bold;color:{tc};">'
                                f'{self.kids(ch)}</p>')
                else:
                    body.append(self.node(ch))
            tds.append(f'<td width="50%" valign="top" bgcolor="{bg}" style="background-color:{bg};'
                       f'border:1px solid {bd};padding:11px 13px;font-size:10pt;width:50%;">'
                       f'{"".join(body)}</td>')
        self.bump('exam')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:10px 0;"><tr>' + ''.join(tds) + '</tr></table>')

    # ---- 小结框 ----
    def summary(self, n):
        hd = n.find_child('hd')
        title = self.kids(hd).strip() if hd is not None else '本章小结'
        rest = self.kids_except(n, {hd} if hd else set())
        self.bump('summary')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:18px 0;"><tr>'
                f'<td bgcolor="{self.T.SUM_BG}" style="background-color:{self.T.SUM_BG};'
                f'border:2px solid {self.T.P};padding:14px 18px;">'
                f'<p style="margin:0 0 8px 0;font-size:12pt;font-weight:bold;color:{self.T.PD};">'
                f'{title}</p>{rest}</td></tr></table>')

    # ---- 数据表 ----
    def table(self, n):
        trs = [c for c in n.children if c.tag == 'tr']
        if not trs:
            trs = [x for c in n.children if c.tag in ('thead', 'tbody', 'tfoot')
                   for x in c.children if x.tag == 'tr']
        has_th = any(any(c.tag == 'th' for c in tr.children) for tr in trs)
        rows = []
        for i, tr in enumerate(trs):
            tds = [c for c in tr.children if c.tag in ('td', 'th')]
            if not tds:
                continue
            cells = []
            for c in tds:
                w = ''
                m = re.search(r'width\s*:\s*([^;]+)', c.attrs.get('style', ''))
                if m:
                    w = f' width="{m.group(1).strip()}"'
                rs = f' rowspan="{c.attrs["rowspan"]}"' if c.attrs.get('rowspan') else ''
                cs = f' colspan="{c.attrs["colspan"]}"' if c.attrs.get('colspan') else ''
                extra = self.clean_style(c.attrs.get('style', ''))
                if c.tag == 'th':
                    P = self.T.P
                    st = (f'background-color:{P};color:#ffffff;font-weight:bold;'
                          'padding:6px 8px;text-align:left;border:1px solid ' + P + ';')
                    cells.append(f'<th{w}{rs}{cs} bgcolor="{P}" style="{st}">{self.kids(c)}</th>')
                else:
                    zebra = (i % 2 == 0) if has_th else (i % 2 == 1)
                    st = 'padding:6px 8px;border:1px solid #dde5e8;vertical-align:top;'
                    if extra:
                        st += extra + ';'
                    bg = ''
                    if zebra:
                        bg = ' bgcolor="#f7fafd"'
                        st += 'background-color:#f7fafd;'
                    cells.append(f'<td{w}{rs}{cs}{bg} style="{st}">{self.kids(c)}</td>')
            rows.append('<tr>' + ''.join(cells) + '</tr>')
        self.bump('table')
        return ('<table width="100%" border="1" cellspacing="0" cellpadding="0" '
                'style="border-collapse:collapse;width:100%;margin:10px 0;font-size:10pt;">'
                + ''.join(rows) + '</table>')

    # ---- 封面 ----
    def cover(self, n):
        def get(name):
            return next((c for c in n.children if c.tag != '#text' and c.has(name)), None)

        norm = lambda s: re.sub(r'[ \t\r\n]+', ' ', s).strip()
        kick = get('kicker')
        kick_txt = norm(self.kids(kick)) if kick else ''
        h1 = next((c for c in n.children if c.tag == 'h1'), None)
        title = sub = ''
        if h1 is not None:
            for c in h1.children:
                if c.tag == 'small':
                    sub = norm(self.kids(c))
                elif c.tag == '#text':
                    title += c.attrs.get('data', '')
                else:
                    title += self.node(c)
        title, sub = norm(title), norm(sub)
        src = get('src')
        src_html = self.kids(src) if src is not None else ''
        tag = get('tagrow')
        tags = ''
        if tag is not None:
            tags = '　｜　'.join(norm(self.kids(c)) for c in tag.children if c.tag == 'span')
        # 深底封面上的 strong 统一反白，避免深底深字
        src_html = src_html.replace('<strong style="color:#0c2038;">',
                                    '<strong style="color:#ffffff;">')
        self.bump('cover')
        PD = self.T.PD
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:0 0 14px 0;"><tr>'
                f'<td bgcolor="{PD}" style="background-color:{PD};padding:22px 24px;">'
                f'<p style="margin:0 0 10px 0;color:#e6f0f0;font-size:10pt;letter-spacing:2pt;">'
                f'{kick_txt}</p>'
                f'<p style="margin:0;color:#ffffff;font-size:23pt;font-weight:bold;">{title}</p>'
                f'<p style="margin:6px 0 0 0;color:#e2eaea;font-size:12.5pt;">{sub}</p>'
                f'<p style="margin:12px 0 0 0;color:#eef4f4;font-size:9.5pt;line-height:1.7;">'
                f'{src_html}</p>'
                f'<p style="margin:10px 0 0 0;color:#e2eaea;font-size:9pt;">{tags}</p>'
                '</td></tr></table>')

    # ---- 文内目录（三级：节分组 / 一级 / 二级）----
    def toc_html(self):
        tiers = self.toc_tiers or [('l1', t) for t in self.toc_items]
        segs = []
        for tier, txt in tiers:
            if tier == 'grp':
                segs.append(f'<p style="margin:8px 0 2px 0;color:{self.T.P};font-weight:bold;'
                            f'font-size:9.5pt;">{txt}</p>')
            elif tier == 'l2':
                segs.append('<p style="margin:2px 0;color:#4a5764;font-size:10pt;'
                            f'padding-left:14px;">· {txt}</p>')
            else:
                segs.append(f'<p style="margin:2px 0;color:#4a5764;font-size:10pt;">· {txt}</p>')
        items = ''.join(segs)
        meta = ''.join(f'{line}<br>' for line in self.toc_meta.splitlines()).rstrip('<br>')
        return ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;'
                'width:100%;margin:0 0 16px 0;"><tr>'
                f'<td bgcolor="{self.T.CARD_BG}" style="background-color:{self.T.CARD_BG};'
                'border:1px solid #dde5e8;padding:12px 16px;">'
                f'<p style="margin:0 0 6px 0;color:{self.T.PD};font-weight:bold;font-size:11pt;'
                f'letter-spacing:2pt;">{self.toc_title}</p>'
                '<p style="margin:0 0 8px 0;color:#4a5764;font-size:9pt;line-height:1.6;">'
                f'{meta}</p>{items}</td></tr></table>')


HEAD_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta charset="utf-8">
<meta name="generator" content="OneNote-friendly export">
<title>{title}</title>
<style>
body{{font-family:'Microsoft YaHei','微软雅黑','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif;
 color:#1b2430;font-size:11pt;line-height:1.7;margin:14px 18px;}}
p{{margin:6px 0;}}
ul,ol{{margin:6px 0 6px 0;padding-left:22px;}}
li{{margin:3px 0;}}
h1,h2,h3,h4{{color:{pd};font-weight:bold;}}
h2{{font-size:17pt;margin:22px 0 4px 0;}}
h3{{font-size:13.5pt;margin:15px 0 4px 0;}}
h4{{font-size:11.5pt;margin:11px 0 4px 0;}}
strong{{color:#0c2038;}}
table{{border-collapse:collapse;}}
th,td{{font-size:10pt;}}
a{{color:{p};text-decoration:underline;}}
code{{font-family:Consolas,'Courier New',monospace;background-color:#eef3f4;color:#0b4a4a;}}
@page{{size:A4;margin:1.6cm;}}
</style>
</head>
<body>
"""

DEFAULT_NOTE = ('说明：本文件为 OneNote 导入优化版（原网页的固定侧边目录已改为上方的文内目录，'
                '浮动/动画效果移除，其余内容与原讲义一致）。')


def run_check(out, label='输出'):
    """OneNote 形态自检：6 项残留 + 标签配对。返回异常项数。"""
    print(f'\n== 自检 {label} ==')
    bad = 0
    for pat, desc in [('class="', 'class 属性残留'), ('var(--', 'CSS 变量残留'),
                      ('linear-gradient', '渐变残留'), ('position:sticky', 'sticky 残留'),
                      ('::before', '伪元素残留'), ('px"', 'px 字号残留')]:
        n = out.count(pat)
        print(f'  {"OK " if n == 0 else "!! "}{desc}: {n}')
        bad += n
    for tag in ('table', 'tr', 'td', 'th', 'p', 'span', 'ul', 'ol', 'li', 'h2', 'h3', 'h4'):
        o = len(re.findall(rf'<{tag}[ >]', out))
        c = len(re.findall(rf'</{tag}>', out))
        if o != c:
            print(f'  !! {tag} 标签不匹配: open={o} close={c}')
            bad += 1
    if bad == 0:
        print('  OK 全部检查通过（0 class / 0 残留 / 标签全平衡）')
    else:
        print(f'  !! 共 {bad} 项异常')
    return bad


def main():
    ap = argparse.ArgumentParser(description='讲义 HTML → OneNote 导入适配版')
    ap.add_argument('--src', help='输入：网页版讲义 HTML')
    ap.add_argument('--out', help='输出：OneNote 适配版 HTML')
    ap.add_argument('--primary', default='#0f6b6b', help='主题色，其余色自动派生')
    ap.add_argument('--primary-dark', default=None, help='主题深色（默认自动派生）')
    ap.add_argument('--accent', default='#b8860b', help='章节编号 ［x］ 前缀色')
    ap.add_argument('--title', default=None, help='输出文件 <title>（默认取源 <title> 加后缀）')
    ap.add_argument('--toc-meta', default=None,
                    help='文内目录的说明行（<br> 分隔，可多行）；默认从源侧栏 .meta 自动提取')
    ap.add_argument('--note', default=DEFAULT_NOTE, help='封面下方的说明行；传空串可去掉')
    ap.add_argument('--check', action='store_true', help='转换后执行自检并打印报告')
    ap.add_argument('--check-only', metavar='FILE', default=None,
                    help='仅自检：对已成型的 OneNote 形态 HTML（如 references/examples/ 下的体例蓝本）'
                         '执行同样的自检，不做转换')
    a = ap.parse_args()

    if a.check_only:
        p = a.check_only
        if not os.path.isfile(p):
            sys.exit(f'!! 文件不存在：{p}')
        run_check(open(p, encoding='utf-8').read(), os.path.basename(p))
        return

    if not a.src or not a.out:
        ap.error('--src 与 --out 为必填（或改用 --check-only FILE）')

    raw = open(a.src, encoding='utf-8').read()
    src_title = re.search(r'<title>(.*?)</title>', raw, re.S)
    src_title = src_title.group(1).strip() if src_title else os.path.basename(a.src)
    out_title = a.title or f'{src_title}（OneNote 版）'

    theme = Theme(a.primary, a.primary_dark, a.accent)
    b = Builder()
    b.feed(raw)
    conv = Converter(theme, note=a.note)
    if a.toc_meta is not None:
        conv.toc_meta = a.toc_meta
        conv.toc_meta_locked = True

    def find(node, *cs):
        for ch in node.children:
            if ch.tag != '#text' and ch.has(*cs):
                return ch
            r = find(ch, *cs)
            if r is not None:
                return r
        return None

    cov = find(b.root, 'cover')
    cover = conv.cover(cov) if cov is not None else ''
    if cov is not None:
        cov.attrs['_skip'] = '1'
    body = conv.kids(b.root)

    note_html = (f'<p style="margin:0 0 12px 0;color:#7a8b96;font-size:8.5pt;">{a.note}</p>'
                 if a.note else '')
    out = (HEAD_TMPL.format(title=out_title, p=theme.P, pd=theme.PD)
           + cover + note_html + conv.toc_html() + body + '</body>\n</html>\n')
    open(a.out, 'w', encoding='utf-8').write(out)

    print(f'[to_onenote] 写出 {a.out}（{len(out)} 字符）')
    print('  主题色 primary=%s  dark=%s' % (theme.P, theme.PD))
    print('  结构统计: ' + '  '.join(f'{k}={v}' for k, v in sorted(conv.stats.items())))
    if not conv.toc_items:
        print('  !! 警告：未从源文件侧栏解析到目录条目，文内目录为空')

    if a.check:
        run_check(out, os.path.basename(a.out))


if __name__ == '__main__':
    main()
