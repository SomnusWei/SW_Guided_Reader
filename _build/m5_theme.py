# -*- coding: utf-8 -*-
"""M5：把 Reader 的硬编码配色迁到令牌层，生成 static/theme.css。

做法（可重复执行、可逆）：
  1. 扫 styles.css / screens.css / notes.css 里所有颜色字面量：
     `#rgb` / `#rgba` / `#rrggbb` / `#rrggbbaa` / `rgb(...)` / `rgba(...)`；
  2. 每个颜色生成一个令牌 `--t-<hex>`（hex 自带 alpha 时用 `--t-<hex8>`）：
     亮色 = 原值（等于今天的观感，零回归）；暗色 = 同色相、亮度沿色阶保序反相；
  3. 源文件里的颜色字面量替换成 `var(--t-<hex>)`；
  4. 附加 `color-scheme` 与 `--page-dim`：PDF 页面只压暗、不反色。

为什么用「逐值令牌 + 亮度反相」而不是人工指定 12 个语义令牌：
现有样式里 550+ 个颜色构成一条"纸—墨"色阶（多档纸色 × 多档墨色 × 少量强调色），
人工归并成 12 个语义变量会压平色阶、毁掉层次；逐值映射能**完整保留对比关系**
（深底浅字翻过来仍是浅底深字），而且完全可逆——`--revert` 即回到原始色值。

亮度映射用 `0.06 + (1-l)*0.88` 而不是直接 `1-l` 后 clamp：
直接 clamp 会把所有近白色（纸、面板、画布，l 在 0.90~0.96）全压到同一个下限，
纸色阶在暗色主题里塌成一片；保序压缩保证「谁比谁亮」的关系在暗色里依然成立。

用法：python _build/m5_theme.py [--revert]
"""
from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "408-guided-reader" / "src" / "reader_service" / "static"
TARGETS = ("styles.css", "screens.css", "notes.css")
THEME = STATIC / "theme.css"

# 长的放前面：8 位必须先于 6 位试，否则 #rrggbbaa 会被截成 #rrggbb 留下 "aa"
HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
# 同时覆盖空格语法 rgb(255 255 255 / 8%) 与逗号语法 rgba(0,0,0,.08)
RGB = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*[ ,]\s*(\d{1,3})\s*[ ,]\s*(\d{1,3})\s*(?:[/,]\s*([0-9.]+%?))?\s*\)"
)
# 已生成的亮色令牌，用于增量合并与回滚
LIGHT_TOKEN = re.compile(r"--t-([0-9a-f]{6,8}):\s*#([0-9a-f]{6,8});")

HEADER = """/* ===========================================================================
   主题令牌层（由 _build/m5_theme.py 生成，不要手改；改配色请改源 CSS 后重跑）
   ---------------------------------------------------------------------------
   亮色 = 原始配色（保证 1.x 观感一字不差）。
   暗色 = 同色相、亮度沿色阶保序反相，所以「深底浅字 / 浅底深字」的对比关系
          在夜间自动成立，不需要逐组件重写规则。
   PDF 页面不反色：只按 --page-dim 压暗，避免把白纸变成黑纸。
   重新生成：python _build/m5_theme.py     回滚：python _build/m5_theme.py --revert
   =========================================================================== */
:root {
  color-scheme: light;
  --page-dim: 1;
  --page-dim-cover: 1;
}
html[data-theme="dark"] {
  color-scheme: dark;
  --page-dim: 0.75;
  --page-dim-cover: 0.9;
}
/* 夜间：PDF 页与页面缩略图只压暗，不改色相 */
html[data-theme="dark"] .page canvas,
html[data-theme="dark"] .page > .page-preview { filter: brightness(var(--page-dim)); }
/* 夜间：封面、插图同样只压暗，避免反色成鬼片 */
html[data-theme="dark"] .book-cover img,
html[data-theme="dark"] .cover img { filter: brightness(var(--page-dim-cover)); }
/* 柔和纸色是用户显式选择的纸感，优先级高于页面压暗 */
html[data-theme="dark"] .reader.soft-paper .page :is(canvas, .page-preview) {
  filter: brightness(calc(var(--page-dim) * .96)) sepia(.13);
}
"""


def expand(value: str) -> str:
    value = value.lower()
    if len(value) in (3, 4):
        return "".join(ch * 2 for ch in value)
    return value


def dark_of(rgb: str) -> str:
    """同色相，亮度按色阶保序反相；近中性色保持中性，避免暗色莫名泛彩。"""
    r = int(rgb[0:2], 16) / 255
    g = int(rgb[2:4], 16) / 255
    b = int(rgb[4:6], 16) / 255
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    inverted = 0.06 + (1.0 - l) * 0.88          # l=1 → .06，l=0 → .94，保序不塌陷
    if max(r, g, b) - min(r, g, b) < 0.02:      # 真正的中性灰才去色
        s = 0.0
    else:
        s = min(s, 0.98) * 0.85                 # 暗底上降一点饱和，避免荧光
    r2, g2, b2 = colorsys.hls_to_rgb(h, inverted, s)
    return "{:02x}{:02x}{:02x}".format(
        round(r2 * 255), round(g2 * 255), round(b2 * 255)
    )


def _rgb_token(match: re.Match) -> str:
    r, g, b = (min(int(match.group(i)), 255) for i in (1, 2, 3))
    return "{:02x}{:02x}{:02x}".format(r, g, b)


SHADOW_ALPHA = 0.6
"""低于这个不透明度的暗色一律当阴影/蒙层处理。

阴影和蒙层与主题无关——浅色主题里是"黑色半透明压暗"，暗色主题里仍然是压暗。
如果跟着反相，`rgba(0,0,0,.28)` 会变成 `rgba(240,240,240,.28)`，
所有卡片的投影会变成一圈白色光晕，比不做暗色主题还难看。
"""


def _alpha_of(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw[:-1]) / 100.0 if raw.endswith("%") else float(raw)
    except ValueError:
        return None


def is_shadow(rgb: str, alpha: float | None) -> bool:
    """低透明度的暗色 = 阴影/蒙层，保持字面量、不进令牌层。"""
    if alpha is None or alpha >= SHADOW_ALPHA:
        return False
    lum = 0.299 * int(rgb[0:2], 16) + 0.587 * int(rgb[2:4], 16) + 0.114 * int(rgb[4:6], 16)
    return lum < 90


def scan() -> dict[str, str]:
    """{令牌后缀: 亮色 #hex}，覆盖三种源文件里需要换肤的颜色字面量。"""
    table: dict[str, str] = {}
    for name in TARGETS:
        text = (STATIC / name).read_text(encoding="utf-8")
        for match in HEX.finditer(text):
            full = expand(match.group(1))
            alpha = int(full[6:8], 16) / 255.0 if len(full) == 8 else None
            if is_shadow(full[:6], alpha):
                continue
            table[full] = "#" + full
        for match in RGB.finditer(text):
            rgb = _rgb_token(match)
            if is_shadow(rgb, _alpha_of(match.group(4))):
                continue
            table[rgb] = "#" + rgb
    return table


def existing_light() -> dict[str, str]:
    """读回 theme.css 里已生成的亮色令牌，保证增量迁移不丢已有令牌。

    只读第一个 `:root` 块：第二个块是暗色值，混进来会把暗色当亮色、
    下次生成时再反相一次（双重反相），整个主题就花了。
    """
    if not THEME.is_file():
        return {}
    text = THEME.read_text(encoding="utf-8")
    start = text.find(":root {")
    if start < 0:
        return {}
    end = text.find("}", start)
    block = text[start:end if end > start else len(text)]
    return {
        match.group(1): "#" + match.group(2)
        for match in LIGHT_TOKEN.finditer(block)
    }


def write_theme(table: dict[str, str]) -> None:
    """table: {令牌后缀: 亮色 #hex}"""
    light = []
    dark = []
    for name in sorted(table):
        value = table[name].lstrip("#")
        light.append(f"  --t-{name}: #{value};")
        if len(value) == 8:
            dark.append(f"  --t-{name}: #{dark_of(value[:6])}{value[6:]};")
        else:
            dark.append(f"  --t-{name}: #{dark_of(value)};")
    THEME.write_text(
        HEADER
        + ":root {\n" + "\n".join(light) + "\n}\n"
        + 'html[data-theme="dark"] {\n' + "\n".join(dark) + "\n}\n",
        encoding="utf-8",
    )


def _sub_hex(text: str) -> tuple[str, int]:
    def repl(match: re.Match) -> str:
        full = expand(match.group(1))
        alpha = int(full[6:8], 16) / 255.0 if len(full) == 8 else None
        if is_shadow(full[:6], alpha):
            return match.group(0)
        return f"var(--t-{full})"

    return HEX.subn(repl, text)


def _alpha_pct(alpha: str) -> str:
    """`8%`→`8`，`.28`→`28`，`0.75`→`75`。"""
    alpha = alpha.strip()
    if alpha.endswith("%"):
        return alpha[:-1]
    value = float(alpha) * 100
    return str(int(value)) if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


def _mix_expr(token: str, alpha: str) -> str:
    # rgb(#hex / α) 是非法 CSS（var 替换后 rgb() 不接受 hex），
    # 必须用 color-mix 表达「令牌色 + 透明度」。
    return f"color-mix(in srgb, var(--t-{token}) {_alpha_pct(alpha)}%, transparent)"


def _sub_rgb(text: str) -> tuple[str, int]:
    def repl(match: re.Match) -> str:
        alpha = match.group(4)
        token = _rgb_token(match)
        if is_shadow(token, _alpha_of(alpha)):
            return match.group(0)
        if alpha:
            return _mix_expr(token, alpha)
        return f"rgb(var(--t-{token}))"

    return RGB.subn(repl, text)


ALPHA_MIX = re.compile(
    r"rgb\(var\((--t-[0-9a-fA-F]{3,8})\)\s*/\s*([0-9]*\.?[0-9]+%?)\)")


def fix_alpha_mixes() -> int:
    """把历史版本生成的 `rgb(var(--t-x) / α)`（非法 CSS）就地改写为 color-mix。"""
    total = 0
    for name in TARGETS:
        path = STATIC / name
        text = path.read_text(encoding="utf-8")

        def repl(match: re.Match) -> str:
            return _mix_expr(match.group(1)[4:], match.group(2))

        text, n = ALPHA_MIX.subn(repl, text)
        if n:
            path.write_text(text, encoding="utf-8")
        total += n
        print(f"{name}: color-mix 修正 {n} 处")
    print(f"共修正 {total} 处")
    return 0


def migrate() -> int:
    fresh = scan()
    merged = dict(existing_light())
    merged.update(fresh)
    if not merged:
        print("没有需要迁移的颜色字面量（可能已经迁移过）")
        return 0
    write_theme(merged)
    total = 0
    for name in TARGETS:
        path = STATIC / name
        text = path.read_text(encoding="utf-8")
        text, n_hex = _sub_hex(text)
        text, n_rgb = _sub_rgb(text)
        path.write_text(text, encoding="utf-8")
        total += n_hex + n_rgb
        print(f"{name}: hex {n_hex} 处 / rgb {n_rgb} 处")
    print(f"令牌 {len(merged)} 个 → {THEME.name}；共替换 {total} 处")
    return 0


def revert() -> int:
    """把 `var(--t-xxxxxx)` 还原成原始颜色字面量（只用于回滚）。"""
    if not THEME.is_file():
        raise SystemExit("theme.css 不存在，无法回滚")
    table: dict[str, str] = {}
    for match in LIGHT_TOKEN.finditer(THEME.read_text(encoding="utf-8")):
        table.setdefault(match.group(1), match.group(2))
    total = 0
    for name in TARGETS:
        path = STATIC / name
        text = path.read_text(encoding="utf-8")
        for key, value in table.items():
            # 先还原 rgb()/color-mix 包装，否则会留下 rgb(#xxxxxx / 8%) 这种非法值
            rgb_args = "{},{},{}".format(
                int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
            )
            text, n1 = re.subn(
                rf"rgb\(var\(--t-{key}\)\s*/\s*([0-9.]+%?)\)",
                lambda m, a=rgb_args: f"rgba({a},{m.group(1)})",
                text,
            )
            text, n1b = re.subn(
                rf"color-mix\(in srgb, var\(--t-{key}\) ([0-9.]+)%, transparent\)",
                lambda m, a=rgb_args: f"rgba({a},{float(m.group(1)) / 100:g})",
                text,
            )
            text, n2 = re.subn(rf"rgb\(var\(--t-{key}\)\)", f"rgb({rgb_args})", text)
            text, n3 = re.subn(rf"var\(--t-{key}\)", f"#{value}", text)
            total += n1 + n1b + n2 + n3
        path.write_text(text, encoding="utf-8")
    THEME.unlink(missing_ok=True)
    print(f"已回滚 {total} 处，theme.css 已删除")
    return 0


def main() -> int:
    if "--revert" in sys.argv:
        return revert()
    if "--fix-alpha" in sys.argv:
        return fix_alpha_mixes()
    return migrate()


if __name__ == "__main__":
    sys.exit(main())
