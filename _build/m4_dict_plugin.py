# -*- coding: utf-8 -*-
"""把 tools/dict/辞典.html 转成 plugins/dict/index.html（Reader 插件形态）。

变换只有四处，其余原样保留（词典检索逻辑一行不改）：
  1. 图标路径 → 插件自己的 icon.svg；
  2. 补一套白天主题变量（原页面是硬编码深色）；
  3. 启动时把 ?text= 当作 ?q= 处理（§3.2 要求首屏出结果）；
  4. 末尾挂插件契约脚本：message 二次触发 + 「在书中搜此词」回传。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "tools" / "dict" / "辞典.html"
DST = REPO / "408-guided-reader" / "plugins" / "dict" / "index.html"

LIGHT_THEME = """
  /* ---- Reader 插件：白天主题（原页面为硬编码深色，这里覆盖主要表面） ---- */
  html[data-theme="light"]{
    --bg:#faf8f3; --panel:#ffffff; --panel2:#f1eee7; --line:#ddd6c9;
    --ink:#23201b; --ink2:#6d675c; --hl:#ffe08a; --ok:#1c8a5e;
    --accent:#5a4fcf; --accent2:#4638b8;
  }
  html[data-theme="light"] header{background:linear-gradient(180deg,#ffffff,#f4f1ea)}
  html[data-theme="light"] .tabs{background:#f7f4ee !important}
  html[data-theme="light"] #status{background:#f4f1ea; color:#6d675c}
  html[data-theme="light"] .hit{border-bottom-color:#eee9df}
  html[data-theme="light"] .hit:hover{background:#f1eee7}
  html[data-theme="light"] .hit.on{background:#e7e1ff}
  html[data-theme="light"] .hit .d, html[data-theme="light"] .art .src,
  html[data-theme="light"] .card .m, html[data-theme="light"] .card .g{color:#6d675c}
  html[data-theme="light"] mark{background:var(--hl); color:#23201b}
  html[data-theme="light"] .art .src .pill, html[data-theme="light"] .chip:hover{background:#f1eee7}
  html[data-theme="light"] .chip{background:#ffffff; color:#3a352c}
  html[data-theme="light"] .acts button{background:#f7f4ee; color:#3a352c}
  html[data-theme="light"] .tab{color:#4c4740}
  html[data-theme="light"] ::-webkit-scrollbar-thumb{background:#d8d2c6}
  html[data-theme="light"] .lt-plugin-bar button{background:#ffffff; color:#3a352c; border-color:#ddd6c9}
"""

PLUGIN_CSS = """
  .lt-plugin-bar{position:fixed; right:14px; bottom:34px; z-index:20}
  .lt-plugin-bar button{
    padding:7px 13px; cursor:pointer; font-family:inherit; font-size:12px;
    color:var(--ink); background:var(--panel2); border:1px solid var(--line);
    border-radius:8px; box-shadow:0 4px 14px rgb(0 0 0 / 18%);
  }
  .lt-plugin-bar button:hover{border-color:var(--accent); color:var(--accent2)}
"""

BOOTSTRAP = """
<script>
/* Reader 插件契约（方案 §3.2）：
   - ?action=&text= 首屏就出结果（text 已在上面 boot 里当 ?q= 处理）；
   - message 收到 lt-plugin-context 时换词重查，不要求用户再点一次。 */
(function () {
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
  }
  function currentQuery() {
    var box = document.getElementById('q');
    return box ? box.value.trim() : '';
  }
  function retrigger(text) {
    var box = document.getElementById('q');
    if (!box || !text) return;
    box.value = text;
    box.dispatchEvent(new Event('input', { bubbles: true }));
  }

  var params = new URLSearchParams(location.search);
  applyTheme(params.get('theme') || 'dark');

  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data || data.type !== 'lt-plugin-context') return;
    applyTheme(data.theme);
    retrigger(data.text);
  });

  document.addEventListener('DOMContentLoaded', function () {
    var bar = document.createElement('div');
    bar.className = 'lt-plugin-bar';
    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = '在书中搜此词';
    button.addEventListener('click', function () {
      var text = currentQuery();
      if (!text) return;
      parent.postMessage({ type: 'lt-plugin-event', event: 'search-in-book', text: text }, '*');
    });
    bar.appendChild(button);
    document.body.appendChild(bar);
  });
})();
</script>
"""


def main() -> int:
    if not SRC.is_file():
        print(f"找不到源文件：{SRC}")
        return 2
    html = SRC.read_text(encoding="utf-8")

    # 1) 图标
    html = html.replace(
        '<link rel="icon" type="image/png" href="../../icon.png">',
        '<link rel="icon" type="image/svg+xml" href="icon.svg">',
    )
    html = html.replace(
        "<title>中医辞典 · 离线</title>",
        "<title>中医辞典</title>",
    )

    # 2) 主题 + 插件样式（插在第一个 </style> 前）
    html = html.replace("</style>", LIGHT_THEME + PLUGIN_CSS + "</style>", 1)

    # 3) ?text= 当 ?q= 用
    before = html
    html = html.replace(
        "    const dq = usp.get('q');",
        "    const dq = usp.get('q') || usp.get('text');",
    )
    assert html != before, "没找到 boot 里的 dq 取值处"

    # 4) 启动时应用主题（boot 的第一行之前插入）
    before = html
    html = html.replace(
        "(async function boot() {\n  const t0 = performance.now();",
        "(async function boot() {\n  document.documentElement.dataset.theme = "
        "new URLSearchParams(location.search).get('theme') === 'light' ? 'light' : 'dark';\n"
        "  const t0 = performance.now();",
    )
    assert html != before, "没找到 boot 函数头"

    # 5) 契约脚本
    html = html.replace("</body>", BOOTSTRAP + "</body>", 1)

    # 6) 载入失败的提示改为插件口径
    html = html.replace(
        "请确认 <code>tools/dict/data/</code> 下有 index.json 与各词典文件。",
        "请确认插件目录 <code>plugins/dict/data/</code> 下有 index.json 与各词典文件。",
    )

    DST.write_text(html, encoding="utf-8")
    print(f"生成 {DST}（{len(html)} 字符）")
    for needle in ("usp.get('q') || usp.get('text')", "lt-plugin-context", "data-theme", "在书中搜此词"):
        print(f"  检查 {needle!r}: {'OK' if needle in html else 'MISSING'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
