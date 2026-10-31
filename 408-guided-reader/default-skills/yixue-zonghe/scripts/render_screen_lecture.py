#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讲义「屏幕连续版」渲染器

把一份 OneNote 版讲义 HTML 转成适合平板笔记 App（华为笔记 / StarNote 的无界笔记）
手写批注的连续版形态，输出三样东西：

  1) <名>_屏幕版.html          左窄栏目录（仅在开头出现一次）＋ 右宽栏正文，连续流式、无 A4 分页
  2) <名>_屏幕版_图/NN_xxxxxpx.png   超长 PNG，按「块边界」切片（不切断任何表格/段落），
                                     默认每片 ≤16000px（避开 WebP 单边上限与移动端解码上限）
  3) <名>_屏幕版.pdf           长页 PDF，一片一页，文字可搜索（与图分片一一对应）

用法：
  python render_screen_lecture.py <讲义_OneNote版.html> [选项]

选项：
  --out-dir DIR       产物目录（默认：源文件同目录下的「<名>_屏幕版」）
  --width N           画布总宽，默认 1600
  --rail N            左栏目录宽，默认 300
  --zoom F            正文字号整体放大倍率，默认 1.6
  --dpr N             截图设备像素比，默认 1（平板上想放大细看可设 2，体积/内存数倍增长）
  --max-slice N       单张长图最大高度，默认 16000
  --formats LIST      输出形式，逗号分隔，默认 png,pdf（可只出 png 或只出 pdf）
  --title T           覆盖讲义名（默认取源文件 <title>）

依赖：playwright（Chromium）、Pillow

实现要点（改这块代码前务必先读）：
  * 截图不能靠「放大视口 + full_page」——实测页面高到 8 万 px 时 Chromium 会漏画远离首屏的区域
    （后半段整片空白）。本脚本改成**逐片渲染**：每片只显示该片内的块，其余 display:none，
    这样每次 full_page 截图都 ≤ max-slice，稳稳在安全范围内。
  * 切点一律落在「块与块之间」，绝不切断表格或段落；块本身超过上限时单独成片并告警。
"""
import argparse
import html as html_mod
import re
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # 长图必然超过 Pillow 默认的 1.79 亿像素警戒线

TAG_RE = re.compile(r"<[^>]+>")

HIDE_SCRIPT = """(keep) => {
    const blocks = [...document.getElementById('zoom').children];
    blocks.forEach((el, i) => { el.style.display = keep.includes(i) ? '' : 'none'; });
}"""


def strip_tags(s: str) -> str:
    s = TAG_RE.sub("", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def split_document(src: str):
    """拆出源文档的 <style> 内容与 <body> 内层 HTML"""
    styles = re.findall(r"<style[^>]*>(.*?)</style>", src, re.S | re.I)
    style = "\n".join(styles)
    style = re.sub(r"@page\s*\{[^}]*\}", "", style, flags=re.I)  # 屏幕版不能带 A4 分页
    m = re.search(r"<body[^>]*>(.*)</body>", src, re.S | re.I)
    return style, (m.group(1) if m else src)


def build_rail(body: str) -> str:
    """由正文的 h2/h3 生成左栏目录（两级）；进入附录后不再收 h3"""
    items, in_appendix = [], False
    for m in re.finditer(r"<h([23])\b[^>]*>(.*?)</h\1>", body, re.S | re.I):
        lvl, text = int(m.group(1)), strip_tags(m.group(2)).replace("▍", "").strip()
        if not text:
            continue
        if lvl == 2:
            if "附录" in text:
                in_appendix = True
            items.append((2, text))
        elif not in_appendix:
            items.append((3, text))
    rows = []
    for lvl, text in items:
        cls = "rail-item rail-lv2" if lvl == 2 else "rail-item rail-lv3"
        rows.append(f'<p class="{cls}">{html_mod.escape(text)}</p>')
    return "\n".join(rows)


def build_screen_html(src: str, *, title: str, total_w: int, rail_w: int,
                      zoom: float, main_pad: int = 30) -> str:
    style, body = split_document(src)

    # 「目录」搬到左栏，正文里的 OneNote 说明行与文内目录表不再重复
    body = re.sub(r"<p[^>]*>说明：本文件为 OneNote 导入优化版.*?</p>", "", body, flags=re.S)
    body = re.sub(
        r"<table[^>]*>(?:(?!</table>).)*?目\s*录(?:(?!</table>).)*?</table>",
        "", body, count=1, flags=re.S)
    body = body.replace("OneNote 适配版", "屏幕连续版")

    inner_w = int((total_w - rail_w - 2 * main_pad) / zoom)
    rail = build_rail(body)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html_mod.escape(title)}（屏幕连续版）</title>
<style>
{style}
/* ===== 屏幕连续版版式：左窄栏目录 + 右宽栏正文，连续流式、无分页 ===== */
html,body{{margin:0;padding:0;background:#ffffff;}}
body{{margin:0;}}
#screen-root{{display:flex;align-items:stretch;width:{total_w}px;background:#ffffff;}}
#rail{{flex:0 0 {rail_w}px;width:{rail_w}px;box-sizing:border-box;
       background:#f3f7fc;border-right:3px solid #014198;padding:26px 16px;}}
.rail-title{{margin:0 0 4px 0;color:#012d6a;font-size:13pt;font-weight:bold;line-height:1.45;}}
.rail-sub{{margin:0 0 16px 0;color:#7a8b96;font-size:8.5pt;line-height:1.6;}}
.rail-group{{margin:16px 0 6px 0;color:#014198;font-size:10pt;font-weight:bold;
             border-bottom:1px solid #c5d6ea;padding-bottom:4px;}}
.rail-item{{margin:4px 0;color:#33475b;font-size:9.5pt;line-height:1.55;}}
.rail-lv2{{color:#012d6a;font-weight:bold;}}
.rail-lv3{{padding-left:10px;color:#4a5764;}}
#main{{flex:0 0 auto;width:{total_w - rail_w}px;box-sizing:border-box;
       padding:26px {main_pad}px 40px {main_pad}px;}}
#zoom{{width:{inner_w}px;zoom:{zoom};}}
</style>
</head>
<body>
<div id="screen-root">
  <div id="rail">
    <p class="rail-title">{html_mod.escape(title)}</p>
    <p class="rail-sub">屏幕连续版　·　左侧目录 / 右侧正文<br>适合导入平板笔记 App 的无界笔记手写批注</p>
    <p class="rail-group">目 录</p>
{rail}
  </div>
  <div id="main"><div id="zoom">
{body}
  </div></div>
</div>
</body>
</html>
"""


def measure_blocks(page):
    """正文全部顶层块（含零高元素，保证 JS 索引与之一致）：底边 + 高度，均为视觉像素"""
    return page.evaluate("""() => [...document.getElementById('zoom').children].map((el, i) => {
        const r = el.getBoundingClientRect();
        return { i, bottom: Math.round(r.bottom + window.scrollY), h: Math.round(r.height) };
    })""")


def plan_slices(blocks, max_slice):
    """按块边界切片，返回 [(y0, y1, 块索引列表, 是否超限)]"""
    end = max((b["bottom"] for b in blocks), default=0)
    if end <= 0:
        return []
    cuts, start, prev = [], 0, 0
    for b in sorted(blocks, key=lambda x: x["bottom"]):
        y = b["bottom"]
        if y - start <= max_slice:
            prev = y
            continue
        # 装不下：先收尾到「上一个块边界」
        if prev > start:
            cuts.append((start, prev, False))
            start = prev
        # 回退到边界后要重新判断——只有单个块本身超限才允许整片超限
        if y - start > max_slice:
            cuts.append((start, y, True))
            start = y
        prev = y
    if start < end:
        cuts.append((start, end, False))

    out = []
    for y0, y1, oversize in cuts:
        idx = [b["i"] for b in blocks if y0 < b["bottom"] <= y1]
        if not idx:  # 兜底：取底边最接近 y1 的块
            cand = [b for b in blocks if b["bottom"] >= y1]
            idx = [cand[0]["i"]] if cand else []
        out.append((y0, y1, idx, oversize))
    return out


def main():
    ap = argparse.ArgumentParser(description="讲义屏幕连续版渲染器")
    ap.add_argument("src")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--rail", type=int, default=300)
    ap.add_argument("--zoom", type=float, default=1.6)
    ap.add_argument("--dpr", type=int, default=1)
    ap.add_argument("--max-slice", type=int, default=16000)
    ap.add_argument("--formats", default="png,pdf")
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    src_path = Path(args.src).resolve()
    if not src_path.exists():
        print(f"找不到源文件：{src_path}")
        return 1
    raw = src_path.read_text(encoding="utf-8")
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    title = args.title or (strip_tags(m.group(1)) if m else src_path.stem)
    title = re.sub(r"（.*?版）", "", title).strip()

    out_dir = (Path(args.out_dir).resolve() if args.out_dir
               else src_path.parent / f"{src_path.stem}_屏幕版")
    img_dir = out_dir / "图"
    out_dir.mkdir(parents=True, exist_ok=True)

    screen_html = build_screen_html(raw, title=title, total_w=args.width,
                                    rail_w=args.rail, zoom=args.zoom)
    html_path = out_dir / f"{src_path.stem}_屏幕版.html"
    html_path.write_text(screen_html, encoding="utf-8")
    print(f"① 屏幕版 HTML：{html_path}")

    from playwright.sync_api import sync_playwright

    formats = {f.strip() for f in args.formats.split(",") if f.strip()}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": 1200},
                                device_scale_factor=args.dpr)
        page.goto(html_path.as_uri(), wait_until="load")
        page.wait_for_timeout(400)

        blocks = measure_blocks(page)
        # 逐片渲染时每片会额外带上 #main 的上下内边距与首块外边距，故规划时先扣掉安全余量
        PAD_BUDGET = 150
        budget = max(4000, args.max_slice - PAD_BUDGET)
        slices = plan_slices(blocks, budget)
        content_end = max((b["bottom"] for b in blocks), default=0)
        n_over = sum(1 for s in slices if s[3])
        print(f"② 画布 {args.width} × {content_end}px @{args.dpr}x；正文块 {len(blocks)} 个；"
              f"切片 {len(slices)} 片（成图每片 ≤ {args.max_slice}px）")
        if n_over:
            print(f"   ⚠️ 有 {n_over} 片因单个块自身超过上限而超限，请人工确认这几处")

        # ---- 逐片长图：每次只显示本片的块，full_page 截图即得到该片 ----
        if "png" in formats:
            img_dir.mkdir(parents=True, exist_ok=True)
            for i, (y0, y1, idx, oversize) in enumerate(slices, 1):
                page.evaluate(HIDE_SCRIPT, idx)
                page.wait_for_timeout(60)
                # 片内实际高度（隐藏了片外块之后）；full_page 会补到「视口高」，
                # 故截图后按实际高度裁掉多余白边
                real_h = page.evaluate("document.documentElement.scrollHeight")
                name = f"{i:02d}_{y1 - y0}px{'_超限' if oversize else ''}.png"
                path = img_dir / name
                page.screenshot(path=str(path), full_page=True)
                with Image.open(path) as im:
                    if im.height > real_h * args.dpr:
                        im.crop((0, 0, im.width, real_h * args.dpr)).save(path)
                with Image.open(path) as im:
                    size = im.size
                    lo, hi = im.convert("L").getextrema()
                warn = "（单块超限）" if oversize else ""
                if size[1] > args.max_slice and not oversize:
                    warn += f"（超出 {args.max_slice}px 上限！）"
                blank = "  ← 空片，请检查！" if lo == hi else ""
                print(f"   图 {i:02d}: y {y0}–{y1}（{y1-y0}px）{warn} → {name} "
                      f"{size[0]}×{size[1]} {path.stat().st_size/1024/1024:.1f}MB{blank}")
            page.evaluate(HIDE_SCRIPT, [b["i"] for b in blocks])  # 还原

        # ---- 长页 PDF：一片一页（Chrome 打印分页），文字可搜索 ----
        if "pdf" in formats:
            page_h = args.max_slice
            page.add_style_tag(content=(
                f"@media print{{@page{{size:{args.width}px {page_h}px;margin:0;}}}}"))
            page.evaluate("""(ys) => {
                const blocks = [...document.getElementById('zoom').children];
                for (const y of ys) {
                    const el = blocks.find(e => {
                        const r = e.getBoundingClientRect();
                        return Math.round(r.top + window.scrollY) >= y;
                    });
                    if (el) el.style.breakBefore = 'page';
                }
            }""", [s[0] for s in slices[1:]])
            pdf_path = out_dir / f"{src_path.stem}_屏幕版.pdf"
            page.pdf(path=str(pdf_path), width=f"{args.width}px", height=f"{page_h}px",
                     print_background=True,
                     margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            nb = pdf_path.read_bytes()
            npage = len(re.findall(rb"/Type\s*/Page[^s]", nb))
            print(f"③ 长页 PDF：{pdf_path}（页宽 {args.width}px × 页高 {page_h}px，"
                  f"{npage} 页，{pdf_path.stat().st_size/1024/1024:.1f}MB）")

        browser.close()

    print(f"完成。产物目录：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
