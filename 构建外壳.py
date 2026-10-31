# -*- coding: utf-8 -*-
"""构建外壳：把根目录 index.html（启动页母本）转成 app/dist/index.html。

2.0 起外壳只剩一个「启动页」：拉起 Reader sidecar、拿到 READY 地址、主窗口直接
跳过去。首页里不再有工具网格，所以本脚本也从"逐处改 TOOLS 结构"退化成一次通用
变换：

  1. head 里插入引导脚本（Tauri 的 HTML 处理管线会静默吞掉 body 尾部 <script>，
     因此主脚本以 type="text/plain" 存放，由引导脚本在 DOMContentLoaded 后执行）；
  2. 主脚本前面加全局错误捕获，脚本自身报错也能显示出来。

用法：  python 构建外壳.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "index.html"
DST = ROOT / "app" / "dist" / "index.html"

BOOTSTRAP = """<script>
  /* =====================================================================
     启动页引导脚本（必须放在 head）：
     Tauri 的 HTML 处理管线会静默吞掉 body 尾部的 <script>（file:// 下正常，
     tauri:// 下不执行且无任何报错），所以主脚本以 type="text/plain" 存放，
     由这里在 DOMContentLoaded 后取出执行；同时注册全局错误捕获。
     ===================================================================== */
  window.__showBootErr = function (text) {
    var el = document.getElementById('__errbox');
    if (!el) {
      el = document.createElement('pre');
      el.id = '__errbox';
      el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#a00;color:#fff;font-size:12px;padding:8px;white-space:pre-wrap;max-height:50%;overflow:auto;';
      document.body.appendChild(el);
    }
    el.textContent += text + '\\n';
  };
  /* 禁用 WebView2 原生右键菜单（返回/刷新/另存为/检查），与 Reader 页面
     同一策略：window 冒泡阶段兜底 preventDefault，输入控件不拦。 */
  window.addEventListener('contextmenu', function (event) {
    var target = event.target;
    if (target instanceof Element && target.closest('input, textarea, select')) return;
    if (target instanceof Element && target.isContentEditable) return;
    event.preventDefault();
  });
  document.addEventListener('DOMContentLoaded', function () {
    var mainEl = document.getElementById('__mainCode');
    if (!mainEl) { window.__showBootErr('启动页初始化失败：找不到主脚本。'); return; }
    try {
      (new Function(mainEl.textContent))();
    } catch (err) {
      window.__showBootErr('启动页初始化失败：' + String(err && err.stack || err).slice(0, 800));
    }
  });
</script>
"""

MAIN_PRELUDE = """window.addEventListener('error', function (e) {
  window.__showBootErr('页面脚本错误: ' + e.message + ' @' + (e.lineno || '?'));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__showBootErr('未处理的 Promise 错误: ' + String(e.reason && e.reason.stack || e.reason).slice(0, 500));
});
"""

HEAD_ANCHOR = '<meta charset="utf-8">\n'
MAIN_ANCHOR = "<script>\n// __MAIN__\n"


def replace_once(text: str, old: str, new: str, tag: str) -> str:
    count = text.count(old)
    if count != 1:
        print(f"[错误] 锚点「{tag}」出现 {count} 次（期望 1 次），启动页结构变了，请检查脚本。",
              file=sys.stderr)
        sys.exit(1)
    return text.replace(old, new, 1)


def build_html() -> str:
    html = SRC.read_text(encoding="utf-8")
    html = replace_once(html, HEAD_ANCHOR, HEAD_ANCHOR + BOOTSTRAP, "meta charset")
    html = replace_once(
        html,
        MAIN_ANCHOR,
        '<script type="text/plain" id="__mainCode">\n' + MAIN_PRELUDE,
        "主脚本标记 // __MAIN__",
    )
    return html


def main() -> None:
    html = build_html()
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(html, encoding="utf-8", newline="\n")
    print(f"已生成 {DST.relative_to(ROOT)}（{len(html)} 字符）")


if __name__ == "__main__":
    main()
