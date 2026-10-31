# LearningTools 2.0 升级实施方案

> 版本：v2.0.0（大版本，不兼容 1.x 的"工具集"信息架构）｜ 后续：v2.1.0 教材检索（已立项，见 §13）
> 编写日期：2026-10-30 ｜ 修订：2026-10-30（第一版评审意见落地，见 §14；同日追加 2.1 章节）
> 目标读者：本项目唯一开发者（你）+ 后续接手的 AI 助手
> 状态：**已评审通过，可开工**。A / B / C 三项决策已定；D 未明确反对，按建议执行（§11.3）

---

## 0. 一页结论

| 维度 | 1.3.1 现状 | 2.0 目标 |
|---|---|---|
| 应用身份 | 「学习工具集」外壳 + 4 个 iframe 工具，Reader 是其中之一 | **Reader 就是应用本体**，启动即进入书库 |
| 工具形态 | 硬编码在 `index.html` 的 `TOOLS` 常量里，无法装/卸 | **插件**：目录 + `plugin.json`，可安装 / 停用 / 卸载 |
| 拼音 & 辞典 | 独立页面，选区操作只 `postMessage` 给平台，Reader 独立窗口下**根本用不了**（`app.js:4942` 直接提示"需要在平台主窗口内使用"） | 注册进 **PDF 选区菜单**，点击在 Reader 内**弹窗**查看结果 |
| 教学内容 | 行间教学 + KP（章节学习地图 / Master 知识点对话） | **全部删除**（保留导读 guide、AI 助手、批注、笔记、搜索、框选） |
| 主题 | 单一"纸色"浅色 | 白天 / 黑夜 / 跟随系统 |
| 设置 | 一个长滚动弹窗（批注写回、OCR、SKILL、AI 密钥、快捷键 5 段平铺） | **6 个标签页**：通用 / 插件管理 / SKILL 管理 / AI 设置 / 快捷键 / 关于 |
| 默认内容 | 无 | 中医辞典、拼音标注**两插件随包**并默认启用；`yixue-zonghe` **认知层随包**（5.4MB，装完即可用），资料层（1.37GB 教材）不随包、可选挂载 |

**技术路线一句话**：把"壳"掏空成启动器，把所有能力（插件注册表、插件静态服务、设置、主题）下沉到 Reader 侧车（Python sidecar）——因为插件要出现在 PDF 选区菜单里、要在 Reader 内弹窗，跨源（Tauri 窗口 ↔ 127.0.0.1）方案会引入 IPC 授权、CSP、同源策略三重麻烦，而 sidecar 与 Reader 页面**天然同源**。

**版本路线**：**2.0**（§1–§12）= Reader 本体 + 插件化 + 双主题 + 设置重构，不含任何"阅读之外"的智能能力；**2.1**（§13）= 教材检索，让 AI 真能引教材原文并标出出处。两者之间只有 6 个接口契约（§13.13），2.0 实施时顺手留好即可，**不增加 2.0 的工作量**。

### 0.1 项目定位（2.0 的身份定义，先定这件事再谈实现）

**一句话：2.0 的项目本体 = 一个 Tauri 的 Reader 独立应用；辞典、拼音、SKILL 都是挂在它身上的附加品。**

| 层 | 是什么 | 在 2.0 中的地位 | 可否缺失 |
|---|---|---|---|
| **核心** | Tauri 外壳（`app/`，Rust）+ Reader sidecar（Python，约 3.5 万行）+ `static/` 前端 | **应用本体**，启动即书库，卸载其中任一环应用即不可用 | 否 |
| **插件** | `plugins/<id>/`（辞典 161MB、拼音）+ `plugin.json` 契约 | Reader 的**扩展**：进 PDF 选区菜单、在 Reader 内弹窗 | 是（全卸掉 Reader 照常读 PDF） |
| **技能** | `SKILL.md` + `references/` 认知层（5.4MB，随包）+ 可选资料库（1.37GB 教材，不随包） | 只被 AI 问答消费的**文本层**，不进任何 UI 主流程 | 是（无技能时退回通用解释） |

由此产生四条贯穿全文的硬约束：

1. **"学习工具集""平台首页""工具网格"这些概念在 2.0 彻底消失**——`TOOLS` 常量、iframe 宿主、首页卡片、`tools/*.html` 全部删除，不是"隐藏"。1.x 的 `learningtools-main`（壳 + 平台页）与 `408-guided-reader`（Reader）在逻辑上是**同一个产品的两部分**，2.0 之后唯一入口是 Reader。
2. **主次不可倒置**：任何"插件没装好导致 Reader 起不来"的设计都是错的。插件注册失败 → 记日志 + 设置里标"插件损坏" → 功能区少两个按钮，**阅读、批注、笔记、AI 必须完全不受影响**。
3. **体积预算归 Reader 本体**：安装包主体是 sidecar（约 70MB）+ 随包插件（约 165MB）+ 技能认知层种子（5.4MB）+ NSIS 压缩 → 预计 **~550MB**。教材资料层（1.37GB）不参与打包，所以安装包大小与"用户搬不搬教材"完全解耦——换新机、装完就能用，不会因为资料缺失而功能残缺。
4. **命名与标识**：产品名、窗口标题、关于页统一为 **Guided Reader**；但 `tauri.conf.json` 的 `identifier`、NSIS `installMode`、exe 文件名**保持 1.x 原值不动**——这是"覆盖升级不丢数据"的前提，改名放到 3.0 再评估。

> **物理仓库**：2.0 不改 git 仓库结构（`learningtools-main/` 与 `408-guided-reader/` 仍并存，构建脚本跨目录取料）。"逻辑单一项目"通过 §2.3 的目标目录布局 + 启动链唯一化来表达；是否把两个目录合并成一个 monorepo，留到 2.0 发版后单独评估（见 §14 遗留项）。

---

## 1. 现状盘点（代码事实，均已核对）

### 1.1 外壳（Tauri + `index.html`）

| 事实 | 位置 |
|---|---|
| 工具注册表是前端常量数组，改它需改 HTML | `index.html:379-416`（`const TOOLS = [...]`，4 项：reader / notes / pinyin / dict） |
| 工具以 iframe 承载，切换即显示对应 `frame-wrap` | `index.html:432`（`frames` Map）、`.host` / `.frame-wrap` |
| 右侧栏/搜索/首页卡片全部围绕 `TOOLS` 生成 | `index.html:452-510`、`781` |
| 启动即首页（"今天用哪个工具？"），Reader 需手动点卡片再点"启动 Reader" | `index.html:332-339`、`tools/reader.html` |
| Tauri 主窗口加载 `index.html`，Reader 是**另开的一个 Tauri 窗口**（label `reader`，加载外部 URL） | `app/src-tauri/src/lib.rs:387-398`（`open_reader_window`） |
| Reader 数据目录选取顺序：便携 `exe 旁/Reader` → `%APPDATA%/LearningTools/Reader` | `lib.rs:53-77`（`pick_data_dir`） |
| sidecar 启动参数：`--no-open --port 随机 --data-dir <dir>`，读 stdout 的 `READY` 行拿 URL | `lib.rs:244-284` |
| 打包资源：`binaries/reader-service/`、`binaries/dailyread-skill/` | `app/src-tauri/tauri.conf.json:34-37` |
| 版本号三处同步：`tauri.conf.json` / `Cargo.toml` / `package.json` | `发布安装版.ps1:116-139` |

### 1.2 Reader 侧车（Python，约 3.5 万行）

```
reader_service/
├── server.py            路由总表（贴片式正则匹配，~2200 行）
├── __main__.py          启动装配：LibraryService / Foundation / Outline / Annotation
│                        / Knowledge / Teaching / Assistant / Learning / Skills / Notes
├── annotation/          批注 + PDF 写回（pdf_sync.py）
├── assistant/           AI 问答（provider 运行时、历史、流式）
├── foundation/          页面渲染、OCR（UmiOcrEngine）、文字层
├── knowledge/           ★ KP：章节学习地图（service 1125 行 / repository 965 / semantic 814）
├── learning/            ★ KP 配套：Master 知识点对话、掌握状态（service/schema/repository）
├── teaching/            导读 service（保留） + inline_* 行间教学（★ 删除）
├── skills/              SKILL.md 注册表（327 行，纯文本层，不执行脚本）
├── notes.py             笔记（保留，是 Reader 的一部分，不是插件）
└── static/              前端（app.js 5420 行 + 40 个模块）
```

关键前端文件：

| 文件 | 作用 | 2.0 处置 |
|---|---|---|
| `app.js` | 主控：屏幕切换、选区、批注、AI、面板 | 大改（拆选区动作注册、主题、设置入口） |
| `settings-ui.js` | 设置弹窗（235 行，5 段平铺） | 重写为 6 标签 |
| `inline-ui.js` / `inline-placement.js` | 行间教学渲染与排版预留 | **删除** |
| `inlineReserve()`（`app.js:623`）及 12 处调用 | 行间教学打开时给页面留 332px | **删除**，连带 `computeDirectoryMode` 签名简化 |
| `master-ui.js`（~650 行） | 知识点对话、掌握状态 | **删除** |
| `guide-ui.js`（348 行） | 章节导读 | 保留 |
| `selection.js` / `annotation-ui.js` | PDF 选区与批注菜单 | 改造（动态插件动作） |
| `skills-ui.js` / `ai-keys-ui.js` / `shortcuts-ui.js` | 现有三段设置 | 复用到新标签页 |
| `styles.css` / `screens.css` / `notes.css` | **约 988 处硬编码颜色**（523 / 410 / 55） | 主题化的主要工作量 |

### 1.3 拼音 / 辞典当前的真实状态

- `tools/拼音标注.html`（12KB）+ `tools/pinyin-pro.js`（316KB）
- `tools/dict/辞典.html`（26KB）+ `tools/dict/data/`（**161MB**，按 chunk 懒加载 JSON）
- Reader 选区菜单里已有「拼音」「词典」两个按钮（`static/index.html:241-242`），但实现是：

```js
// app.js:4939-4945
if (!window.parent || window.parent === window) {
  announce("拼音/词典联动需要在平台主窗口内使用。", true);
  return;
}
window.parent.postMessage({ type: "open-tool", tool, text }, "*");
```

→ 由于 Reader 现在**是独立窗口**，`window.parent === window` 恒成立，这两个按钮在 1.3.1 里**点了只会报错**。2.0 正好把这条链重做成插件内弹窗。

### 1.4 已有能力（2.0 必须原样保留）

PDF 导入/渲染、目录、书内搜索、框选解释、文本框/区域高亮/便签、全书批注面板、批注写回 PDF（OFF/MANUAL/AUTO）、笔记抽屉（含页码带入、块编辑器实时渲染）、AI 问答（多层追问、保存整段解释、选中片段存摘录）、笔记独立小窗、快捷键、AI 密钥、SKILL 选择器、OCR 状态提示、阅读显示（柔和纸色/竖屏）、缩放。

### 1.5 测试基线（改动时用来判"有没有变坏"）

- `pytest`：全绿（实测 `collect` **702 项**，含 `test_saved_explanations` / `test_annotation_pdf_sync` / `test_export_md`）
- `npm test`：**19 项既有失败**（环境性，与本机 WebView/依赖有关），改动后的失败集合必须与基线**逐项相同**
- `tests-e2e/*.mjs`：Playwright + Edge 真实页面驱动（`notes-live-render`、`assistant-excerpt-menu` 等）
- 增量包交付：`发布增量包.ps1`（.py 改动自动重建 sidecar）+ `apply.ps1` + SHA256 校验；坑：PyInstaller 日志走 stderr 会被 PS 5.1 当致命错误，需 `cmd /c "... 2>nul"` 包裹

---

## 2. 目标架构

### 2.1 进程与窗口模型

```
learningtools.exe（Tauri）
│  启动 → 拉起 sidecar → 拿到 READY URL → 主窗口直接导航到该 URL
│  （窗口 label = main，整块 WebView 就是 Reader 界面；不再有"首页"）
│
└── reader-service.exe --data-dir <dir> --port <随机> --plan …
     ├── /                      书库 + 阅读器（现有 SPA）
     ├── /notes.html            笔记（独立小窗仍可开，也可在主窗口以抽屉存在）
     ├── /plugins/<id>/…        ★ 新增：插件静态资源（同源）
     └── /api/plugins…          ★ 新增：插件注册表、启停、安装、卸载
```

**为什么不做"插件 = 独立 Tauri 窗口"**：Reader 页面在 `http://127.0.0.1:port`，要调 Tauri 命令得给远程域开 IPC 授权（capabilities `remote.urls`），且 Tauri 窗口之间传选区文本还得再走一层；而同源 iframe 弹窗零成本，还能复用 Reader 的样式中性、Esc 关闭、焦点管理。**结论：插件一律在 Reader 内以 `<dialog>` + iframe 呈现**，需要"更大"时同弹窗内全屏，不新开原生窗口。

**主窗口导航的落地细节**（`lib.rs`）：
1. `index.html`（保留但瘦身成启动页）→ `invoke('reader_url')` → `location.replace(url)`；
2. 若导航被 Tauri 拦截或失败，回退到现有"另开 reader 窗口 + 隐藏主窗口"路径（保留代码，加开关 `LT_LAUNCH_MODE=boot|window`）；
3. 主窗口标题改为"Guided Reader"；启动页背景色与 Reader 一致，避免白闪。

### 2.2 插件宿主三件套

| 组件 | 位置 | 职责 |
|---|---|---|
| 注册表 `PluginRegistry` | 新增 `reader_service/plugins/registry.py` | 扫描两个根、校验 manifest、合并启用状态、安装/卸载 |
| 静态服务 | `server.py` `_served_file()` 扩展 | `/plugins/<id>/<relpath>` 仅允许 manifest 声明的 `entry` 同目录树，扩展名白名单（html/js/css/json/svg/png/woff2/wasm） |
| 前端宿主 `plugin-host.js` | 新增 `static/plugin-host.js` | 拉注册表 → 渲染选区动作 → 打开弹窗 iframe → 注入上下文 → 收结果回传 |

### 2.3 目录布局

```
<安装目录>/                          ← Reader 本体 + 随包插件 + 技能认知层种子（不含教材资料）
├── learningtools.exe                ← 名字沿用 1.x（覆盖升级不丢数据）
├── reader-service/                  ← Python sidecar（约 70MB，含内置 assistant-explanation 技能）
├── plugins/                         ★ 随包插件（只读：可停用，不可卸载）
│   ├── dict/plugin.json + index.html + data/    (161MB，已定：直接随包)
│   └── pinyin/plugin.json + index.html + pinyin-pro.js
├── default-skills/                  ★ 随包技能种子（只读，首启复制到数据目录）
│   └── yixue-zonghe/                SKILL.md + references/ + templates/ + scripts/ + 仓颉/  (5.4MB)
└── Reader/                          数据目录（便携模式，原样不动）
    ├── state.sqlite3
    ├── blobs/                       PDF 库（默认根目录）
    ├── skills/                      已安装 SKILL（首启从 default-skills 复制进来）
    │   ├── config.json              enabled 状态（现有）
    │   └── library.json             ★ 资料库挂载登记（只记路径，不复制；§6.5）
    ├── plugins-state.json           ★ 插件启用状态 + 用户插件根标记
    ├── settings.json                ★ 主题 / 库目录 / onboarding 标记（与 v27 settings 表互为镜像）
    └── trash/                       ★ 卸载插件/迁移 PDF 的回收目录（不是直接删）

%APPDATA%/LearningTools/plugins/  ★ 用户自装插件（可卸载；便携模式下仍在安装目录）
%APPDATA%/LearningTools/trash/    ★ 用户插件卸载回收目录
```

> `default-skills/` 是只读源，`<data-dir>/skills/` 是可写的落地位置。首启只复制 5.4MB（毫秒级），之后以数据目录为准——用户手改 SKILL.md 不会被安装包覆盖，需要重置时点「恢复内置知识包」。`skills/` 允许为空：删光技能 Reader 照常启动，AI 退回通用解释。

优先级：用户目录同名 id 覆盖随包版本（版本号高者生效，若更低则在设置里提示"被随包版本覆盖"）。

---

## 3. 插件标准（统一开发规范）

### 3.1 `plugin.json`

```json
{
  "schema": 1,
  "id": "dict",
  "name": "中医辞典",
  "version": "1.0.0",
  "description": "九部离线词典，20 万词条，支持词头/全文检索与术语跳查。",
  "author": "local",
  "entry": "index.html",
  "icon": "icon.svg",
  "category": "查检",
  "surface": ["window", "selection"],
  "window": { "width": 980, "height": 720, "title": "中医辞典", "resizable": true },
  "selection": {
    "actions": [
      {
        "id": "lookup",
        "label": "词典",
        "title": "用中医辞典查所选文字",
        "order": 40,
        "accepts": ["text"],
        "maxChars": 60,
        "stripPunctuation": true
      }
    ]
  },
  "permissions": ["read-own-assets"]
}
```

字段约束（注册表强制校验，非法即跳过并在设置里显示"插件损坏"）：

| 字段 | 规则 |
|---|---|
| `id` | `^[a-z][a-z0-9-]{1,31}$`，全局唯一；决定 URL `/plugins/<id>/` |
| `entry` | 相对路径，禁止 `..`、绝对路径、`#` |
| `surface` | `window`（可从"插件"菜单打开） / `selection`（进选区菜单） |
| `selection.actions[].order` | 0-99，越小越靠左；与内置动作（复制=10、问 AI=20、高亮=60…）共用序号 |
| `permissions` | 目前只支持 `read-own-assets`；不开放文件系统/网络白名单（后续再扩） |

### 3.2 插件页面通信协议（宿主 ↔ 插件）

宿主注入（iframe `load` 后 `postMessage`，并附在 URL query 上双保险）：

```js
// Reader → 插件
{
  type: "lt-plugin-context",
  action: "lookup",          // 触发它的选区动作 id；纯窗口打开时为 null
  text: "「本虚标实」",       // 清洗后的选区文本（去首尾空白、可选去标点、截断到 maxChars）
  selection: { page: 12, bookId: "…", bookTitle: "中医内科学" },
  theme: "dark",             // light | dark
  locale: "zh-CN"
}

// 插件 → Reader（可选，用于「在书里定位」等反向操作）
{ type: "lt-plugin-event", event: "toast", message: "已复制 3 个词条" }
```

插件页必须做两件事（缺一即视为不合规，`test_plugin_contract.py` 会检查）：

1. 读 URL 参数 `?action=&text=`（`text` 用 `encodeURIComponent`），**首屏就能出结果**，不要求用户二次点击；
2. `window.addEventListener("message", …)` 接收 `lt-plugin-context`，处理"已经在页面里，又换了一段文字"的二次触发。

### 3.3 安装 / 卸载 / 启停

| 动作 | 接口 | 行为 |
|---|---|---|
| 列出 | `GET /api/plugins` | 返回合并后的插件列表：`id,name,version,source(builtin|user),enabled,surface,actions,error?` |
| 启停 | `PUT /api/plugins/<id>/enabled` `{enabled}` | 写 `plugins-state.json`；停用后选区动作立即从菜单消失 |
| 安装 | `POST /api/plugins/install`（multipart 或 `{path}`） | 支持 **.zip**（含 `plugin.json` 在根或唯一顶层目录）与**目录**；校验 id/entry/大小上限（单插件 ≤ 300MB）；解到临时目录→校验→原子 rename 到用户目录 |
| 卸载 | `DELETE /api/plugins/<id>` | 仅 `source=user`；移动到 `trash/<id>-<ts>/`，返回"可恢复"路径 |
| 打开 | 前端 `pluginHost.open(id, {action, text})` | 弹窗 iframe；`window` 类插件在"插件"菜单里列出 |

安装来源交互：设置 →「插件管理」→「安装插件…」→ 原生文件选择（由于在主窗口里没有 Tauri FS 插件，采用 `<input type="file" webkitdirectory>` 选目录 + `<input type="file" accept=".zip">` 上传到 sidecar，由 sidecar 解压落盘）。

### 3.4 安全边界（必须写进实现）

- 插件页与 Reader **同源**（同一 sidecar 端口），因此**不把 launch token 放进插件 URL**；插件页面本身不需要任何 API 权限。
- `/plugins/<id>/…` 只读、只允许 GET/HEAD、禁止目录穿越（`resolve()` 后必须仍在插件根内）、禁止 `dotfile`。
- 插件页不能访问 Reader 的 `/api/*`（服务端校验 `Referer`/`Sec-Fetch-Site` 太脆，改为：**所有 `/api/*` 校验 `X-Reader-Token`，插件页拿不到 token**）。若某插件确实需要，后续引入 `manifest.permissions` + 短期 scoped token，本期不做。

---

## 4. Reader 改造

### 4.1 启动即 Reader

| 改动 | 文件 |
|---|---|
| 主窗口 `index.html` 瘦身为启动页（logo + 进度 + 失败重试），启动后 `reader_url` → `location.replace` | `index.html`（约 780 行 → <120 行） |
| 删除 `TOOLS` 注册表、iframe 宿主、首页卡片/搜索/侧栏导航 | `index.html`、`构建外壳.py`（脚本可大幅简化，或直接废弃、改由 Reader 提供唯一入口） |
| 保留 `open_notes` 命令（笔记小窗），新增 `open_devtools` 调试命令 | `lib.rs` |
| 主窗口标题、图标、启动背景 | `tauri.conf.json`、`index.html` |
| 启动失败可视化（sidecar 起不来 / 端口占用 / 数据目录不可写），仍能进"诊断"页 | `index.html` boot 逻辑 |

> 说明：`tools/reader.html`（166 行的"启动 Reader"中介页）在 2.0 里**没有存在意义**，删除；`构建外壳.py` 的 Tauri 脚本搬移逻辑（`__mainCode`）依旧需要，但注入目标换成新的启动页。

### 4.2 删除行间教学

| 层 | 删除内容 |
|---|---|
| Python | `teaching/inline_service.py`(183) / `inline_contracts.py`(73) / `inline_evidence.py`(60)；`teaching/__init__.py` 导出；`__main__.py` 里的 `TeachingService.inline` 装配；`server.py:266,818` 的 `(guide|inline-teaching)` 路由分支改为只认 `guide` |
| 数据 | `assistant` 的 `source_kind = INLINE_GUIDANCE`：新请求拒绝；历史记录保留可读（不改库） |
| 前端 | 删 `inline-ui.js`(287) / `inline-placement.js`(17)；`app.js` 的 `inline` 实例、`inlineReserve()` 及其 12 处布局调用、`INLINE_GUIDANCE` 文案分支（`app.js:3112`）、`inline.renderPage`（`1405,4071`） |
| 排版 | `computeDirectoryMode({… inlineReserve …})` 去掉该参数，目录排版逻辑随之简化（注意 1.3.1 曾因 grid 列宽踩坑，改完必须回归"收起侧栏/窄窗/竖屏"三种布局） |
| 清理 | `styles.css` / `screens.css` 中 `.inline-*`、`inline-open` 相关规则 |

保留：`teaching/service.py`（导读）、`guide-ui.js`。

### 4.3 删除 KP（知识点）

| 层 | 删除内容 |
|---|---|
| Python | `knowledge/`（repository 965 + semantic 814 + service 1125）、`learning/`（Master 对话与掌握状态）、`__main__.py` 装配、`server.py` 的 `/knowledge-map*`、`/learning*`、`/api/learning/status` 全部路由（约 30 处分支） |
| 前端 | `master-ui.js`（~650 行）、`knowledge-panel` 相关 DOM（`static/index.html:107-112`）与 `app.js` 中 `renderKnowledge*`、`knowledge-*` 元素引用、`.knowledge-*` 样式 |
| 依赖 | `outline` 的 KP 关联字段只读保留（不删列，避免迁移风险）；`export_md.js` 的 `master_topic` 段落删除 |
| 数据 | **不做破坏性迁移**：`knowledge_points*` / `learning_*` 表保留但不再读写；下个大版本再评估物理清理（附 `tools/cleanup_kp_tables.py` 备用脚本，默认不执行） |

> 为什么保留表：删除会让 `state.sqlite3` 迁移不可回滚，而用户已有真实学习记录。保留空转成本几乎为零。

**验收判据**：全量 grep 后 `knowledge`/`learning`/`inline` 在 `static/` 与 `server.py` 中**零命中**（`notes-blocks.js` 里 `inline` 不算，属 CSS 关键字）。

### 4.4 主题：白天 / 黑夜 / 跟随系统

**新增令牌层**（`static/theme.css`，最先加载）：

```css
:root {
  color-scheme: light;
  --paper:#f6f5ee; --canvas:#ebeae3; --line:#ccccc2;
  --ink:#202820; --muted:#737b78; --green:#3b5848;
  --surface:#fffef9; --surface-2:#f1efe6; --overlay:rgb(32 40 32 / 42%);
  --shadow:0 3px 12px rgb(38 51 43 / 9%);
  --danger:#a4463f; --warn:#8a6a2f; --ok:#3c6b4f;
  --page-dim:1;    /* 夜间压暗 PDF 页面：0.85 */
}
html[data-theme="dark"] {
  color-scheme: dark;
  --paper:#1b1d1a; --canvas:#151714; --line:#333730;
  --ink:#e6e6df; --muted:#9aa09a; --green:#9dbfa7;
  --surface:#20231f; --surface-2:#282c26; --overlay:rgb(0 0 0 / 55%);
  --shadow:0 3px 14px rgb(0 0 0 / 45%);
  --danger:#e09189; --warn:#d9b578; --ok:#8fc7a4;
  --page-dim:0.86;
}
```

实施要点：

1. **令牌迁移**：`styles.css`(523) + `screens.css`(410) + `notes.css`(55) 共约 988 处硬编码色 → 归并为上表 12 个变量（脚本辅助 + 人工复核，避免把"PDF 页面白底"也换掉）。
2. **不动 PDF 内容本身**：页面 canvas / `.page-preview` 保持原色，仅夜间 `filter: brightness(var(--page-dim))`；「柔和纸色」开关与主题叠加时后置（`soft-paper` 优先级高于 `page-dim`）。
3. **切换与持久化**：`GET/PUT /api/settings/appearance` `{theme:"light|dark|system"}` 存 `<data-dir>/settings.json`；`system` 用 `matchMedia("(prefers-color-scheme: dark)")` 监听变化，**不轮询**。
4. **首屏防闪**：`index.html` 的 `<head>` 内联一段同步脚本，从 `localStorage` 读上次值先打 `data-theme`，再由 API 校准（避免白屏闪一下黑）。
5. **覆盖页面**：`notes.html`（独立小窗）同样接 `theme.css` + 同源 localStorage key；`plugin-host` 把 `theme` 通过 `lt-plugin-context` 传给插件，并在主题切换时**再推一次**（插件不必重载）。
6. **插件页合规要求**：插件必须定义 `--lt-bg/--lt-fg/--lt-accent` 三变量并使用，否则夜间模式出现"白底插件"——这条写进 §3.2 的契约测试。

**验收**：三种模式 × 4 个界面（书库 / 阅读器 / 笔记载体 / 设置弹窗 + 1 个插件弹窗）截图对比，控制台无 `color-scheme` 警告；切换 < 100ms 无明显重排；`system` 模式下改 Windows 主题应用实时跟随。

---

## 5. 设置界面重构

### 5.1 信息架构

```
设置（全屏 dialog，左侧 6 个标签，右侧内容区）
├── 通用
│   ├── 界面模式           ○ 白天  ○ 黑夜  ○ 跟随系统
│   ├── PDF 文件夹         当前路径 + 「更换…」+「迁移已有文件」（默认 = 现在的 <data-dir>/blobs）
│   ├── OCR 服务状态       绿/灰点 + 引擎名 + 最近检测时间 + 「重新检测」
│   ├── 批注写回 PDF       （原有三档 + 源文件/库内副本，2.0 从"顶层"降为"通用"下的分组）
│   └── 阅读显示           柔和纸色 / 竖屏模式（与顶栏菜单同源，双向同步）
├── 插件管理               列表：图标 · 名称 · 版本 · 来源（随包/用户）· [启用✓] · ⋯（卸载/打开）
│                         底部：「安装插件…」（zip / 目录）
├── SKILL 管理             列表：名称 · 描述 · 触发词数 · [启用✓] · [设为默认] · 删除
│                         底部：「导入 SKILL 目录」
├── AI 设置                各 provider 密钥（现有 ai-keys-ui）+ 默认模型
├── 快捷键                 现有 shortcuts-ui（含 toggle_notes / toggle_annotations 等）
└── 关于                   版本 2.0.0 · 构建时间 · 数据目录（可复制）· 日志目录 · 检查更新 · 开源许可
```

### 5.2 需要新增/调整的接口

| 接口 | 用途 |
|---|---|
| `GET/PUT /api/settings/appearance` | 主题（`theme`） |
| `GET/PUT /api/settings/library` | 读/写 PDF 根目录（`PUT` **只改路径，不迁移**） |
| `POST /api/settings/library/migrate` + `GET …/status` | 显式迁移已有文件（`dryRun` 预检 / 后台任务 + 进度），见 §5.3 |
| `GET /api/settings/ocr` | `{available, engine, version, checked_at, hint}`（复用 `foundation/umi_ocr.py` 的探测） |
| `GET /api/plugins` 等 | 见 §3.3 |
| `GET /api/skills`（已有）、`PUT /api/skills/<n>/enabled`（已有）、`PUT /api/skills/<n>/default`（**新增**） | SKILL 管理 |
| `GET/PUT/DELETE /api/skills/<n>/library`（**新增**） | 资料库挂载登记（只记路径，§6.5） |
| `POST /api/skills/<n>/restore`（**新增**） | 用 `default-skills/` 覆盖被改坏的知识包 |
| `GET /api/onboarding`、`POST /api/onboarding/complete` | 引导状态与完成标记（§6.4） |
| `GET /api/about` | 版本、构建时间、数据目录、日志路径 |

### 5.3 PDF 文件夹设置的边界（**已定：只影响新导入 + 手动迁移**）

语义只有一句话：**换目录 = 换"以后新书放哪"，不动任何已有文件。**

| 场景 | 行为 |
|---|---|
| 升级到 2.0 首次打开设置 | 显示现有路径（`<data-dir>/blobs`），**不弹任何迁移提示**，零行为变化 |
| 用户点「更换…」选了新目录 | 立即生效于**之后导入**的 PDF；页面明确写出"已有 N 本书仍在原目录（现占用 X GB）"，旁边就是「迁移已有文件」按钮 |
| 用户点「迁移已有文件」 | 弹确认框列出文件数与总大小 → 复制到新目录 → 逐文件 SHA256 校验 → 更新库记录（事务）→ 校验全成功后旧文件移入 `trash/migrated-<ts>/`；**保留 7 天**再清理 |
| 迁移中途失败/断电 | 库记录仍在事务内未提交 → 旧文件原样在 → 界面显示"迁移未完成，可重试"；已复制的残留文件记为可清理项，不重复占坑 |
| 目标目录不可写 / 是系统目录 / 盘符根 | 拒绝并给出具体原因（`os.access` + 黑名单 `C:\Windows`、`C:\Program Files`、盘符根） |
| 目标目录已存在同名文件 | 不覆盖，冲突项单独列出让用户选"跳过/改名" |

实现要点：

- `GET/PUT /api/settings/library`：`PUT {path}` 只写 `settings.library.root`；`POST /api/settings/library/migrate {path, dryRun}` 负责迁移，`dryRun=true` 返回清单预估（前端确认框直接用它）。
- 迁移走后台任务 + 轮询进度（`GET /api/settings/library/migrate/status`），避免 2GB 书库把请求挂死。
- 迁移期间**禁止导入新书**（前端置灰 + 后端 409），避免两个目标目录交叉。
- 批注写回 PDF 的"源文件/库内副本"语义不变：库内副本永远在**当前**根目录下；迁移后写回目标随之更新。

### 5.4 设置入口

- Reader 顶栏齿轮（现 `#reader-settings`）打开新设置；`Ctrl+,` 亦可。
- 书库页右上角也放一个（未打开书时也能配插件/主题）。

### 5.5 设置页与阅读器的解耦

现在 `createSettingsUI({api, revisionId, …})` 强依赖当前书（`revisionId`），但 2.0 的通用/插件/SKILL/AI/快捷键/关于**在书库页也要能打开**。改造：`revisionId` 变为可选，与书相关的分组（批注写回、阅读显示）在无书时置灰并提示"打开一本书后可调"。

---

## 6. 默认内容、插件与技能装配

### 6.1 默认清单（**已定**）

| 项 | 类型 | 来源 | 默认 | 说明 |
|---|---|---|---|---|
| 中医辞典 | 插件 | **随包** | 启用 | `plugins/dict/`（161MB 数据直接打进安装包，C 决策） |
| 拼音标注 | 插件 | **随包** | 启用 | `plugins/pinyin/`（改名：`OneNote 拼音标注` → **拼音标注**） |
| yixue-zonghe | SKILL | **认知层随包**（5.4MB）／资料层不随包（1.37GB） | 启用 + 默认 | 首启自动落地，无需用户配置；资料库可选挂载，见 §6.3 |
| assistant-explanation | SKILL | sidecar 内置 | 强制启用（不可禁用） | 现有逻辑，无变化 |

> "默认启用"的准确含义：装完就能用，**不需要用户进设置勾任何东西，也不需要在首启做任何选择**。技能现在跟插件一样开箱可用；唯一可选的动作是"挂载教材资料库"，跳过不影响问答质量（因为 AI 本来就读不到教材文件，见 §6.3 事实一）。

### 6.2 两个插件要改什么

**A. 拼音标注**（`tools/拼音标注.html` → `plugins/pinyin/index.html`）

| 改动 | 细节 |
|---|---|
| 标题/文案 | 去掉 "OneNote"，改「拼音标注」；保留"复制进 OneNote 可保留上标"作为说明而非身份 |
| 主题 | 引入 `--lt-*` 变量，跟随宿主 `theme` |
| 接收上下文 | `?text=` 预填输入框并**自动生成**；`message` 监听二次触发 |
| 结果形态 | 新增"叠字注音"呈现（`<ruby>字<rt>zì</rt></ruby>`），不再只是"字(pīn)"纯文本；提供三种复制格式：纯文本 / 拼音上标（OneNote） / HTML ruby |
| 纠音表 | 现有 19 条中医术语（荥→xíng 等）抽成 JSON，便于用户追加（存 `localStorage`，不写插件目录） |
| 选区动作 | `{"id":"annotate","label":"拼音","order":45,"maxChars":300}` |

**B. 中医辞典**（`tools/dict/辞典.html` → `plugins/dict/index.html`）

| 改动 | 细节 |
|---|---|
| 静态资源路径 | `./data/…` 保持不变即可（因为整目录搬进 `plugins/dict/`，相对路径天然成立） |
| 接收上下文 | `?text=` 直接执行一次词头检索；命中多条时列词条、单条时展开正文；`message` 支持二次查词 |
| 主题 | 跟随 `--lt-*`（原页面为硬编码深色，需为夜间/白天各做一套） |
| 返回 | 「在书中搜索此词」按钮 → `postMessage({event:"search-in-book", text})` → Reader 打开书内搜索并带词 |
| 选区动作 | `{"id":"lookup","label":"词典","order":40,"maxChars":60,"stripPunctuation":true}` |

> **161MB 词典：已定直接随包（C 决策）**。安装包从 ~383MB 涨到 ~550MB（LZMA 对 JSON 压缩率好，实际增量可能只有 60~90MB）。不做"首启解压"也不做下载，理由是：装完即可用、无二次等待、无网络依赖，且这 161MB 正好是"Reader 本体 + 随包插件"体积预算里唯一的大头，其余都是几十 MB 级。
> **随之而来的验收项**：§8.2 打包时必须实测①NSIS 安装耗时 ②覆盖升级耗时 ③安装后首启（词典 161MB 是懒加载 JSON，只读 chunk，理论不影响冷启动，需实测确认首查 < 800ms）。若实测安装耗时 > 3 分钟，再启用备选：随包 zip + 首启解压。

### 6.3 技能资产分层：只随包"认知层"（A 决策修订）

先摆两个实测事实，方案必须建立在它们之上。

**事实一：2.3GB 里真正被用的只有 5.4MB。**
`assistant/skill.py:25-42` 的 `load_skill_body()` 决定了 Reader 喂给模型的技能内容是：`SKILL.md` 正文 + `references/` 的**文件名清单**。教材 PDF/txt、体系扫描件**从不进入提示词**；`scripts/*.py` **从不执行**（`skills/registry.py` 开头即写明"Phase 1 文本层，脚本不执行或安装"）。

| 层 | 内容 | 体积 | 谁消费它 | 2.0 处置 |
|---|---|---|---|---|
| **认知层** | `SKILL.md` 35KB + `references/` 2.3MB（30 文件）+ `templates/` 16KB + `scripts/` 304KB + `仓颉/` 2.7MB | **≈5.4MB** | AI 问答的 system prompt（body + 文件名清单） | **随包**（NSIS 压缩后约 2MB） |
| **资料层** | `中医教材/` 527MB、`西医教材/` 598MB、`石学敏体系/` 214MB、`倪海厦体系/` 6.2MB、`蒸馏产出/` 20MB | ≈1.37GB | **目前无人消费**（AI 读不到，Reader 也没有"打开参考文件"入口） | **不随包**；2.1 做"教材检索"时再引入（§13） |
| **平台层** | `platform/` | 927MB | 与 Reader 无关（WorkBuddy 侧工具与缓存） | 不打包、不迁移 |

**事实二：全新机器上"引用已有目录"根本不成立。**
上一版方案把"引用已有目录"设为默认，是因为开发机恰好存在 `%USERPROFILE%\.workbuddy\skills\yixue-zonghe`。换一台干净机器：这个目录不存在、用户手上也没有 2.3GB 的包，"引用"和"本地安装"两个选项都是空的，只剩"跳过"——等于技能永远装不上。**这是把开发机环境当成了用户环境，必须改。**

**新结论：认知层随包，资料层不随包。** 代价只有 5.4MB（压缩后约 2MB），换来"装完即可用"，且完全不依赖用户机器上是否装过别的 AI 工具。

包内种子 `<安装目录>\default-skills\yixue-zonghe\`：

```
SKILL.md            35KB      + 新增的 Reader 适配段
references/         2.3MB     30 个文件（教材索引 / 讲义样板 / 各医家 integration）
templates/          16KB
scripts/            304KB     14 个 py（Reader 不执行，留给用户手动跑与未来检索）
仓颉/                2.7MB     元技能（SKILL.md 引用 6 处）
──────────────────────────────
合计 ≈ 5.4MB（NSIS 压缩后约 2MB）
```

排除：`__pycache__`、`platform/`、`中医教材/`、`西医教材/`、`石学敏体系/`、`倪海厦体系/`、`蒸馏产出/`。

**必须配套的一项质量修正：种子版 SKILL.md 加 Reader 运行环境声明。**
现有 SKILL.md 大量 instruct 模型"运行 `python scripts/search_textbooks.py --keyword 桂枝汤`"，但 Reader 里的模型既不能执行脚本、也拿不到教材原文 → 它会**编造检索结果并声称查过原文**。种子版在正文开头插入一段：

> 运行环境：本技能运行在 Reader 阅读器的问答中，**没有脚本执行能力，也没有教材原文**。
> 遇到"查教材原文 / 翻到某页"的请求，应明确告知用户"当前环境未挂载教材库"，并用自身医学知识作答，不得声称已检索。

同时把正文里"必须运行脚本""先检索再回答"这类强制措辞改为"若环境支持检索则检索，否则直接作答"。这不是可选优化——不加这段，随包种子等于让模型更自信地胡说。

### 6.4 首启：零配置，只有一个可选动作

因为认知层随包，**技能不再需要用户做任何决策**。三种机器状态覆盖如下：

| 机器状态 | 认知层 | 资料层 | 首启行为 |
|---|---|---|---|
| **全新机**（没装过任何技能、也没有 WorkBuddy） | 首启从 `<安装目录>\default-skills\` 复制到 `<data-dir>\skills\yixue-zonghe`（5.4MB，毫秒级） | 无 | 欢迎页一句"中医知识包已就绪"，跳过不损失任何功能 |
| **本机**（已有 2.3GB 完整目录） | 目标目录已存在 → **不覆盖用户文件**，只补 `SKILL.md` 适配段与缺失的 `references/` | 自动检测到候选路径 | 第 2 步预选"登记资料目录"，可一键登记（0 复制） |
| **1.3.1 升级用户** | `skills/` 里已有旧技能 → 原地保留，仅在缺件时补齐 | 原目录即资料 | **完全不弹**（`onboarding.done` 视为 true） |

**引导只在"从未引导过"时出现一次**（判断依据：`settings.onboarding.done`，不再需要 `skill_prompted`）。三步，`<dialog>` 呈现，可 Esc 关、不阻塞读书：

1. **欢迎**：一句话说明"启动即书库"。从 1.3.1 升级来的显示"已保留你的 N 本书、M 条批注"。
2. **资料库（可选）**：文案必须诚实——
   > AI 问答**不需要**教材文件，已内置的知识包足够开始用。
   > 若你想在本地随时翻教材原文，可以把已有的 `yixue-zonghe` 目录挂上来（只登记路径，不复制、不占空间）。
   
   按钮：`自动检测`（命中则显示路径 + 「挂载」）／`选择目录…`／`跳过`。
3. **完成**：显示"知识包已就绪"+「进入书库」。

**设置里的常驻入口**（非模态卡片，SKILL 管理顶部）：「yixue-zonghe · 已就绪 · 5.4MB」+ 一行资料库状态（已挂载 / 未挂载 + 挂载按钮）。用户以后随时能改，不靠首启那一次。

### 6.5 资料库挂载（只登记、不复制）——2.0 做最小版

先说清一件事：**Reader 目前没有"读资料"的消费方**（AI 不进提示词，也没有"打开参考文件"的 UI）。所以资料库挂载在 2.0 里只是一个**状态标记**，为 2.1 的"教材检索"预留接口。既然如此，就不做上一版方案里那套完整的"外部技能引用"（`external.json` + scan 合并三源 + missing 状态）——它在全新机器上无意义，与随包种子重复，且要改 `SkillRegistry` 的核心扫描逻辑，风险不对等。

2.0 只做这些（约 30 行 + 1 张表）：

| 项 | 内容 |
|---|---|
| 登记文件 | `<data-dir>/skills/library.json`：`{"yixue-zonghe": {"path": "C:\\...\\yixue-zonghe", "added_at": "..."}}` |
| 接口 | `GET/PUT/DELETE /api/skills/<n>/library`（PUT 只写路径，不复制任何文件） |
| 校验 | 绝对路径；`Path.resolve()` 后必须含 `SKILL.md`；拒绝盘符根、`C:\Windows`、`C:\Program Files`；不跟随 junction 跳出 |
| 状态展示 | 已挂载（路径 + 检测到的教材目录名与文件数）／未挂载／路径不可达（灰显，不影响任何功能） |
| 明确不做 | 不把资料路径注入提示词；不做文件索引；不做 AI 检索（2.1） |

**2.1 的检索方案（已立项，完整方案见 §13，2.0 不实现）**：把 `中医教材/*.txt` 切片 → 词法索引（FTS5 + bigram 预切分）→ 提问时检索 top-5 片段塞进 prompt → 回答带 `[n]` 编号并可点开原文。真正的"能查教材原文"是那一步，不是挂载。2.0 只需按 §13.13 留好 6 处接口。

### 6.6 SKILL 管理页（设置第 3 标签）的对应行为

| 行类型 | 显示 | 可用操作 |
|---|---|---|
| 内置 `assistant-explanation` | 徽标「内置」 | 无（不可禁用，现有逻辑） |
| `yixue-zonghe`（随包种子） | 徽标「内置 · 5.4MB」+ 「知识包已就绪」 | 启用✓ / 查看 SKILL.md / 恢复默认（用包内版本覆盖被改过的） |
| 资料库状态（同一卡片第二行） | 「资料库：未挂载」或「已挂载 · 中医教材 711 部」或「路径不可达」 | 挂载 / 更换 / 移除（仅移除登记） |
| 用户自己导入的技能 | 名称 · 大小 · 版本 | 启用✓ / 设为默认 / 删除 |

底部按钮：「导入 SKILL 目录」（走现有 `SkillInstaller`，适合小包）、「恢复内置知识包」（`default-skills` 被误删时的一键修复，也是装机验收的一项）。

---

## 7. 数据与迁移

| 数据 | 位置 | 2.0 处理 |
|---|---|---|
| 书库 / 批注 / 对话 / 笔记 | `<data-dir>/state.sqlite3` | 数据库迁移 `v27`：新增 `settings` 表（key-value）。**不删列、不删表** |
| 技能 | `<data-dir>/skills/` | 保留；首启从 `<安装目录>\default-skills\` 补种（已存在则只补缺件）；新增 `library.json`（资料库挂载登记，§6.5） |
| 插件状态 | `<data-dir>/plugins-state.json` | 新增：`{ "dict": {"enabled": true}, … }` |
| 主题 | `settings` 表 + `localStorage` 镜像 | 新键 `appearance.theme`，默认 `system` |
| PDF 根目录 | `settings` 表 `library.root` | 默认空 = 走现有 `blobs/` 逻辑（**行为零变化**）；语义 = 只影响新导入（§5.3） |
| 引导状态 | `settings` 表 `onboarding.done` | 单一标记；1.3.1 升级用户视为已完成（不弹引导） |
| KP 数据 | 现有表 | 保留只读（§4.3） |
| 旧版 `reader-launch.json` / `shortcuts.json` | 数据目录 | 不动，2.0 继续读 |

**升级兼容**：从 1.3.1 升级 = 覆盖安装（NSIS 同 identifier、同 installMode）→ 首次启动跑 `v27` 迁移 → 插件状态默认全部启用 → 技能目录不变 → 书库路径不变。**不需要用户重新导入任何书**。

---

## 8. 备份、打包与发布

### 8.1 第一步：先备份现有版本（不可跳过）

```powershell
# 1) 代码：打 tag 并推
git tag -a v1.3.1 -m "1.3.1 稳定版（2.0 重构基线）"; git push origin v1.3.1

# 2) 安装目录：整包 zip（排除备份/日志）
Compress-Archive -Path D:\LearningTools\* -DestinationPath E:\backup\LearningTools-1.3.1-安装目录.zip

# 3) 数据：state.sqlite3 + blobs + skills（冷备，先关应用）
#    目标 <E:\backup\LearningTools-1.3.1-数据-YYYYMMDD-HHmm>.zip

# 4) 安装包：成品\安装版\LearningTools-1.3.1-安装包.exe 复制一份到 E:\backup
# 5) 恢复脚本：backup\restore-1.3.1.ps1（解压覆盖 + 校验版本号）
```

备份**必须在改代码之前完成**，并用 `restore-1.3.1.ps1` 在本机实测一次"卸载 2.0 → 装回 1.3.1 → 数据可读"（这条是 2.0 的回滚保险，不能只写不验）。

### 8.2 打包链改造

| 环节 | 现状 | 2.0 |
|---|---|---|
| 外壳 | `构建外壳.py`：根 `index.html` → `app/dist/index.html`（Tauri 脚本搬移） | 改为生成"启动页"，或直接 `frontendDist` 指向新目录；脚本内注释同步更新 |
| 资源 | `binaries/reader-service/`、`binaries/dailyread-skill/` | 增加 `binaries/plugins/`（dict 161MB + pinyin）、`binaries/default-skills/yixue-zonghe/`（**认知层 5.4MB**，不含教材）；`dailyread-skill/` 是否保留待定（见 §14 遗留项） |
| 体积 | ≈383MB | ≈**550MB**（dict 数据 +2MB 技能种子）；打包时记录 NSIS 实际产物大小与安装耗时，作为 §6.2 验收数据 |
| 版本 | `发布安装版.ps1 -Version 2.0.0` 同步三处 | 不变；另在 `about` 接口里读 `tauri.conf.json` 同源版本 |
| 增量包 | `发布增量包.ps1`（git diff → payload） | 需支持**新增目录**（`plugins/`，含 161MB 数据）与**删除文件**的清单（现在只处理修改/新增，删除需显式 `removed[]`） |
| sidecar | PyInstaller onedir | 不变；注意 `plugins` **不打进 exe**，走 Tauri resources（相对 exe 路径解析） |

### 8.3 发布节奏（建议）

1. `2.0.0-alpha.1`：只做 Reader 核心化 + 删除行间/KP（可跑，无插件）→ 自测装机
2. `2.0.0-beta.1`：插件体系 + dict/pinyin 插件 + 设置 6 标签
3. `2.0.0-beta.2`：主题三态 + SKILL 管理（含外部引用/失联）+ 首启引导 + 关于
4. `2.0.0`：全量回归 + 安装包 + GitHub Release

---

## 9. 里程碑与工作量

> 按"单人或单人+AI 协作"估算，不含等待构建的时间损耗。

| 阶段 | 内容 | 交付物 | 预估 |
|---|---|---|---|
| **M0 备份与基线** | §8.1 全流程 + 记录 pytest/npm/e2e 基线 + 打 tag | 备份包、恢复脚本、基线报告 | 0.5 天 |
| **M1 启动即 Reader** | 启动页、`reader_url` 导航、失败回退、窗口标题/图标 | 可运行：双击直接进书库 | 1 天 |
| **M2 删除行间教学 + KP** | Python 模块/路由/装配、前端 UI/排版、样式清理、数据保留策略 | 代码瘦身约 **5.2 千行**（Python 4.3k + 前端 0.96k，另加 app.js 内的调用与样式）；`pytest` 全绿；布局回归 | 2 天 |
| **M3 插件体系** | `plugins/registry.py`（扫描/校验/manifest/启停/安装/卸载/回收站）、静态服务、`plugin-host.js`、选区菜单动态化；**技能种子首启补种**（把 `ensure_default_skills` 改为复制 `default-skills` 整个目录，含 `仓颉/` 等子目录）+ 资料库挂载登记（§6.5） | 单元测试（manifest 校验、路径穿越、zip 安装、补种幂等、挂载校验）+ 端到端（装/停/卸/重启保持） | 3 天 |
| **M4 两个插件改造** | 目录搬迁、`plugin.json`、上下文接收、主题适配、结果呈现、拼音 ruby / 词典词条展开 | 选中「本虚标实」→ 点「词典」→ 弹窗出词条；点「拼音」→ 弹窗出注音 | 2 天 |
| **M5 设置重构 + 主题** | 6 标签（含通用 4 组、插件、SKILL 含知识包/资料库状态、AI、快捷键、关于）、令牌化换肤、首屏防闪、system 跟随 | 三种主题 × 5 界面对比图；设置 6 标签全可用（无书时也能开） | 3 天 |
| **M6 引导 + 打包发布** | 三步引导 `<dialog>` + SKILL 卡片状态、`default-skills` 种子打包、插件资源打包（含 161MB dict）、`2.0.0` 全量构建、静默升级本机、Release | 安装包 + 增量包 + 体积/耗时实测报告 + **干净机装完即用验收** | 2 天 |
| 合计 | | | **≈13.5 天** |

依赖关系：M1 可与 M2 并行（改动文件不重叠）；M3 依赖 M1（主窗口导航后设置/插件在 Reader 内），并顺带产出 §6.5 的技能引用能力；M4 依赖 M3；M5 依赖 M1、M3（技能管理页要用 M3 的引用接口）；M6 依赖 M3、M4、M5（引导页要调用插件与技能两套接口）。

---

## 10. 测试策略

| 层 | 内容 |
|---|---|
| Python 单测 | 插件注册表：manifest 合法/非法（id、entry 穿越、缺字段）、双根优先级、启停持久化、zip 安装与卸载回收；设置：主题读写、库目录校验（拒系统目录/不可写）、迁移 dryRun 清单、about 字段；**技能种子**：补种幂等（已存在不覆盖用户改动）、缺件补齐、`restore` 覆盖、`default-skills` 缺失时降级不报错；**资料库挂载**：绝对路径校验、非技能目录拒绝、移除只删登记不动文件 |
| JS 单测 | `plugin-actions` 排序与内置动作合并、`theme` 解析（system→light/dark）、首屏防闪脚本纯函数化 |
| 端到端（Playwright + Edge） | ① 冷启动直达书库；② 打开书→选词→「词典」弹窗出结果→Esc 关闭；③ 「拼音」弹窗注音正确；④ 设置：切黑夜→关闭重开（持久）→切跟随系统；⑤ 插件：停用 dict→选区菜单无「词典」→启用后恢复；⑥ 卸载用户插件→回收站有目录；⑦ 无 KP/行间入口（`#knowledge-*`、`.inline-*` 查询计数为 0）；⑧ **干净机首启**：删空 `skills/` 后启动→知识包自动就绪→问 AI 能引用技能内容→跳过引导→重启不再弹；⑨ **库目录**：换目录→已有书仍可打开→导入新书落在新目录 |
| 回归 | `pytest` 全量；`npm test` 失败集与基线逐项相同；现有 e2e（notes-live-render、assistant-excerpt-menu）全过 |
| 装机验证 | **干净 Windows 虚拟机**（无 Python、无 Node、无 WorkBuddy、`%USERPROFILE%` 下没有任何 skills 目录）：装完直接问 3 个中医问题（辨证/方剂/针灸），回答须体现技能里的框架；1.3.1 → 2.0.0 覆盖升级（书/批注/笔记/技能都在、不弹引导）；2.0.0 卸载 → 重装；回滚到 1.3.1 数据可读 |
| 性能 | 启动到书库 < 4s（冷）、插件弹窗打开 < 300ms（dict 首查含数据加载 < 800ms） |

---

## 11. 风险、回滚与决策记录

### 11.1 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| 主题令牌化遗漏（988 处硬编码） | 黑夜模式出现白块/灰字 | 自动化脚本先批量替换、再按界面截图逐个核对；加"暗色截图亮度检查"脚本（已有先例：用 93% 亮色像素判黑屏） |
| 删除 KP 时误伤 `outline` | 目录/导读失效 | 只删 `knowledge`/`learning` 的调用点，不动 `outline`；回归「目录/导读/搜索」三项 |
| 插件同源带来的权限风险 | 恶意插件读 `/api/*` | 所有 API 校验 `X-Reader-Token`，插件 URL 不带 token；静态路由禁穿越 |
| 161MB 词典拖慢安装 | 安装/升级变慢 | **已定随包**；打包时实测三次数（新装/覆盖/首启），> 3 分钟才启用备选"随包 zip 首启解压" |
| 技能认知层随包、资料层不随包 | 用户以为"装了技能就能查教材原文"，发现不能查就判定为缺陷 | §6.3 的 SKILL.md 适配段明确写"无教材原文"；引导与 SKILL 卡片都标注"资料库：未挂载（不影响问答）"；2.1 用检索真正解决（§13） |
| 模型声称"已检索教材"（幻觉） | 回答带假页码、假原文，比不答更有害 | 种子版 SKILL.md 注入运行环境声明并删掉"必须运行脚本"措辞；测试里加 3 个探针问题核对回答不含虚构页码 |
| 资料库路径失效 | 挂载状态变灰 | 仅影响"本地翻教材"，问答不受影响；卡片给「重新指定」 |
| 引导弹窗打扰老用户 | 升级后被多问一遍 | 1.3.1 升级用户 `onboarding.done` 视为 true，**完全不弹** |
| 主窗口导航到 127.0.0.1 被 Tauri 拦截 | 启动白屏 | 保留"另开窗口"回退路径；启动页内置诊断入口 |
| 增量包脚本不处理"删除文件" | 升级后残留旧模块 | §8.2 增加 `removed[]`；2.0 用**全量安装包**交付，不强依赖增量包 |

### 11.2 回滚方案

1. 代码：`git reset --hard v1.3.1`（2.0 全程在 `release/2.0` 分支，不合 main 直到 M6 验收）；
2. 应用：装回 `E:\backup\LearningTools-1.3.1-安装包.exe`；
3. 数据：`restore-1.3.1.ps1` 回灌 `state.sqlite3` + `blobs`（**注意**：2.0 迁移过 `v27` 的库不能被 1.3.1 直接读，必须回灌备份，不能只降级程序）。

### 11.3 决策记录（2026-10-30 已定，不再反复）

| # | 决策 | 结论 | 落地位置 |
|---|---|---|---|
| **A** | `yixue-zonghe` 怎么装 | **认知层随包（5.4MB）+ 资料层不随包（1.37GB，可选挂载）**。第二版修订：第一版的"完全不随包、引用开发机目录"在全新机器上无解（没有目录可引），已废 | §6.3–§6.6、§9 M3/M6 |
| **B** | PDF 文件夹设置语义 | **只影响新导入** + 显式「迁移已有文件」按钮（复制→SHA256 校验→更新记录→旧文件进 trash，保留 7 天） | §5.3、§5.2 |
| **C** | 词典 161MB 数据 | **直接随包**（安装包 ~550MB）。不做首启解压、不做下载；实测耗时超阈值才回退 | §6.2、§8.2 |
| **D** | 未启用插件是否留在"插件"菜单 | **灰显并提示"已停用"**（用户能看见自己装了什么）。本项未收到明确意见，按建议执行，M3 阶段如手感不好可改成完全隐藏 | §3.3 |

> 已关闭的议题不再讨论：不再评估"整个 2.3GB 技能随包"、不再评估"外部技能引用（external.json）"、不再评估"Tauri 重写后端"、不再评估"插件开独立窗口"。

---

## 12. 附：改动文件清单（实施时按此逐项打勾）

**新增**
- `reader_service/plugins/__init__.py`、`registry.py`、`installer.py`
- `reader_service/skills/library.py`（资料库挂载登记、路径校验）
- `reader_service/skills/seed.py`（从 `default-skills/` 补种、缺件补齐、restore）
- `static/plugin-host.js`、`static/theme.css`、`static/plugin-actions.js`
- `static/onboarding.js`（三步引导 + SKILL 卡片状态）
- `plugins/dict/{plugin.json,icon.svg,index.html,data/…}`
- `plugins/pinyin/{plugin.json,icon.svg,index.html,pinyin-pro.js}`
- `binaries/default-skills/yixue-zonghe/`（认知层种子 5.4MB：SKILL.md + references + templates + scripts + 仓颉）
- `tests/test_plugin_registry.py`、`tests/test_plugin_install.py`、`tests/test_settings_api.py`、`tests/test_skills_seed.py`
- `tests-js/plugin-actions.test.js`、`tests-js/theme.test.js`
- `tests-e2e/reader-first-launch.mjs`、`tests-e2e/plugin-selection.mjs`、`tests-e2e/settings-tabs.mjs`、`tests-e2e/onboarding-skill.mjs`

**重写 / 大改**
- `index.html`（→ 启动页）、`构建外壳.py`、`app/src-tauri/src/lib.rs`（导航 + 资源注入）
- `static/settings-ui.js`（→ 6 标签）、`static/index.html`（删 KP 面板、动态选区动作、主题脚本）
- `static/app.js`（删 inline/KP、插件动作接入、设置入口、主题应用、引导触发）
- `static/skills-ui.js`（→ SKILL 管理页：引用/失联/推荐卡片/设为默认）
- `static/styles.css` / `screens.css` / `notes.css`（令牌化）

**删除**
- `reader_service/knowledge/*`、`reader_service/learning/*`
- `reader_service/teaching/{inline_service,inline_contracts,inline_evidence}.py`
- `static/{inline-ui,inline-placement,master-ui}.js`
- `tools/reader.html`、`tools/拼音标注.html`、`tools/dict/*`（迁入 `plugins/`）
- `server.py` 中 knowledge/learning/inline 路由分支

**轻改**
- `server.py`（插件路由、技能资料库/restore 路由、设置路由、静态白名单）
- `reader_service/skills/registry.py`（`SkillInstaller` 支持整目录复制，供种子与 restore 使用）
- `__main__.py`（装配精简；`GUIDED_READER_DEFAULT_SKILL_DIR` 指向安装包的 `default-skills/`，首启补种）
- `app/src-tauri/tauri.conf.json`（`resources` 增加 `binaries/default-skills/`、标题、版本 2.0.0）、`发布安装版.ps1`、`发布增量包.ps1`

---

## 13. 2.1 升级内容：教材检索（已立项，2.0 之后做）

> 立项时间：2026-10-30。2.0 只交付"资料库挂载"这个状态标记（§6.5），**检索本身不在 2.0 范围内**；本章是 2.1 的实施方案，供 2.0 实施时预埋接口。

### 13.1 一句话目标与验收

**让 AI 真的能翻教材，并且翻到哪儿说得出来。** 不是"能读到文件"，是"回答里的每一句引用都能点开看到原文"。

三个验收问句（缺一不算完成）：

| # | 提问 | 期望 |
|---|---|---|
| 1 | 「桂枝汤的组成与主治是什么」 | 回答给出组成/主治，句末带 `[1][2]` 编号；点 `[1]` 弹出《伤寒论》对应原文片段与书名·篇名 |
| 2 | 「足三里在《灵枢》里怎么定位」 | 检索命中古籍原文，出处标"《灵枢》· 本输"，不混入现代教材的表述而不标来源 |
| 3 | 「量子纠缠的贝尔不等式」 | 明确回「本地教材中未检索到相关内容」，**不出现任何编号、不编造书名页码** |

**非目标**（明确划出，避免范围膨胀）：不做模型微调；不做联网检索；不把整本书上传给模型（只传 top-k 片段）；不做语义向量训练；不做教材 OCR 纠错工程（扫描件按"未索引"处理）。

### 13.2 硬事实（已实测，不是估计）

| 事实 | 数据 | 对方案的影响 |
|---|---|---|
| 中医教材语料 | 733 个文件 / 527MB，其中 **724 个 txt 共 207MB**，另有 7 pdf、1 epub、1 doc | txt 是唯一现实语料；pdf/epub/doc 在 2.1 只做"跳过并标注"，不做解析 |
| 语料字符量 | 抽样 25 文件测字符/字节 = 0.51 → 全库约 **1.11 亿字符** | 切片规模与索引体积由它决定（§13.8） |
| 编码 | 抽样 40 文件：**39 个 GB18030、1 个 UTF-8**（还有 UTF-8 无 BOM / 截断多字节的坑） | 必须**逐文件探测**，不能全库假定一种编码 |
| 古籍结构 | 大量 `<篇名>` `<目录>` 标记（《神农本草经》单文件 379 个 `<篇名>`） | 是天然的**切章锚点**，比按字数硬切好得多 |
| 西医教材 | 23 个文件 / 598MB，**11 txt + 11 pdf**（pdf 多为扫描件） | 默认不纳入索引，扫描件无文本层 |
| 现有 `scripts/search_textbooks.py` | 整库逐行 grep + 关键词命中，纯 CLI | ❌ Reader 无法调用（模型没有脚本执行能力），这正是当前幻觉的源头 |
| 现有 `scripts/build_textbook_index.py` | 只生成 `references/textbook-index.md`（文件分类清单） | ❌ 不含正文，不能当检索索引 |
| 现有 `scripts/text_utils.py` | 已有 `detect_encoding` / `read_text` / `clean_ancient_markup` / `extract_pdf_text` | ✅ **逻辑可复用，但代码要抄进 sidecar**（不 import 用户数据目录里的脚本，见 §13.13 安全条） |
| **中文分词器的现实**（本机 sqlite 3.53.1 + Python 3.13 实测） | `FTS5 tokenize='trigram'` 建表与写入正常，但**查询 2 字词「中风」「桂枝」「太阳」全部 0 命中**（trigram 要求查询 ≥ 3 字符）；改为 **bigram 预切分 + `tokenize='unicode61'`** 后，「中风/桂枝/足三里/桂枝汤」全部正确召回，`bm25()` 排序可用 | **索引方案定死为 bigram 预切分**——中医术语以 2 字为主（桂枝、中风、太阳、少阳、脉浮），trigram 在这类语料上等于不可用 |

> 为什么不能直接"让 AI 调 search_textbooks.py"：Reader 的模型只有文本进出，没有工具调用通道；即便加通道，全库 grep 一次要几十秒，且 700 个文件的结果无法塞进上下文。检索必须在 sidecar 侧做成索引查询。

### 13.3 架构：离线索引 + 查询时检索

```
资料库目录（2.0 的 library.json 登记）
   │  ① 建索引（后台任务，一次性 + 增量）
   ▼
<data-dir>/library/index-<hash>.sqlite3    ② 检索（每次提问，< 300ms）
   │                                            │
   │                                            ▼
   │                                   top-k 片段 + 出处元数据
   │                                            │
   │                                  ③ 组装 system prompt 的"检索块"
   │                                            ▼
   └──────────────────────────────  AI 回答（带 [1][2] 编号）
                                                │
                                       ④ 前端渲染出处 chip → 点开看原文
```

新增模块（全部在 sidecar 内，不依赖用户目录里的脚本）：

| 文件 | 职责 |
|---|---|
| `reader_service/library/__init__.py` | 对外只暴露 `LibraryIndex`（build / search / stats / rebuild） |
| `reader_service/library/extract.py` | 编码探测（抄 `text_utils.detect_encoding` + `read_text`）、古籍标记清洗（抄 `clean_ancient_markup`）、书名/类目提取 |
| `reader_service/library/chunker.py` | 纯函数：文本 → 片段列表（锚点/标题路径/字符偏移），**可单测、无 IO** |
| `reader_service/library/store.py` | SQLite 读写、schema 迁移、增量判定、级联删除 |
| `reader_service/library/retriever.py` | 查询构造、BM25 排序、类别加权、同义词扩展、`[n]` 编号分配 |
| `reader_service/library/citations.py` | 校验回答里的 `[n]` 是否越界、生成出处面板数据 |

**数据模型**（`index-<hash>.sqlite3`，独立文件，**不塞进 `state.sqlite3`**，避免主库被 500MB 索引拖慢备份与迁移）：

| 表 | 字段 | 说明 |
|---|---|---|
| `documents` | `id, path, title, category, encoding, sha256, mtime, chars, indexed_at` | 增量判定靠 `(path, mtime, size)` |
| `chunks` | `id, doc_id, seq, heading, text, char_start, char_end` | `heading` = `书名 > 卷/章 > 篇名`，出处的"篇名"就来自这里；`text` 存**原文**，供出处面板显示 |
| `chunks_fts` | FTS5(`seg, heading_seg`, tokenize=`unicode61`)，`rowid = chunks.id` | `seg` = 正文的 **bigram 预切分串**（`太阳中风` → `太阳 阳中 中风`），不是原文；原文只在 `chunks.text` 存一份，不重复占空间 |
| `meta` | `key, value` | 语料根路径、段长参数、bigram 版本、构建时间、schema 版本 |

> **检索单元为什么是 bigram 而不是 trigram**：实测 trigram 对 2 字词返回空集，而中医术语以 2 字为主（见 §13.2 最后一行）。bigram 预切分把每个相邻二字组合都变成一个 token，`unicode61` 只管按空格切词——等于**用字级 n-gram 替代词表分词**：不需要 jieba、不需要维护中医词典、2 字词照常召回，且对单字错字有容错（错一个字只废掉相邻两个 bigram，整词分词则会全废）。
>
> 代价：索引里存的是切分串（约为原文字符量的 2.2 倍 token），比 trigram 省一些但仍是主要体积来源（§13.8 已算入）。

> `index-<hash>.sqlite3` 里的 hash = 资料库根路径的 SHA1 前 8 位。换挂载目录 = 换一个索引文件，**旧索引不删不污染**；设置页可列出所有索引并一键清理。

### 13.4 切片规则（检索质量的一半在这里）

优先级：**结构锚点 > 句子边界 > 字数窗口**。

1. **先按结构切**：识别 `<篇名>xxx` / `<目录>` / 空行分段 / 「卷X」「第X章」等标题行 → 得到"章节块"；`<篇名>` 的值直接作为 `heading` 末级。
2. **再按字数切**：章节块超过 **500 字**时滑窗切分，overlap **60 字**；不足 120 字的章节块向后合并（不产生碎片段）。
3. **不切坏句子**：切点回退到最近的 `。！？；\n\n` 之后；回退超过 120 字则放弃回退（防长句死循环）。
4. **古籍清洗**：去掉 `<篇名>` `<目录>` 等标记本身（值保留为 heading）、去掉 `http` 行、去掉连续空行、去掉页眉页脚式重复行（同文件内出现 ≥ 5 次的短行）。
5. **偏移可回溯**：`char_start/char_end` 记录片段在**清洗后文本**中的字符区间；`documents` 另存 `clean_sha1` 与清洗参数，定位时用同一流程复现同一份清洗文本再按偏移换算行号（不回存两份全文）。
6. **丢弃**：清洗后 < 40 字且不含中文的片段（纯数字表格、目录残渣）。

**可逆性硬要求**：同一文件所有 `text` 按 `char_start` 排序拼接后，必须等于"清洗后全文"——这条做成单测（§13.11），防止切片丢字。

### 13.5 检索与排序

**2.1.0 只做词法检索，不做向量。** 理由：向量要么依赖本地模型（几百 MB + 首启加载）、要么依赖用户 AI 密钥联网 embedding（失败即无检索），两者都会让"能不能用"取决于环境。词法检索零依赖、可离线、可解释（能说清"为什么命中"）。

| 环节 | 做法 |
|---|---|
| 查询构造 | 去停用词（的、是、怎么、什么、请问、多少）→ 抽实体（方名/药名/穴名/病名/书名）→ **每个实体做 bigram 切分**，单实体用 `"太阳 阳中 中风"`（隐式 AND = 近似短语），多实体用 `OR` 连接并在排序里按命中实体数加权。**不做中文分词**，避免引入 jieba 与词典维护 |
| 同义词扩展 | 维护 `references/synonyms.json`（约 200 条）：方剂异名（桂枝汤/桂枝加葛根汤）、穴位别名（足三里/下陵）、病名古今对应（消渴/糖尿病）——扩展词以低权重 `OR` 追加，只影响召回不影响排序主分 |
| 排序 | `bm25(chunks_fts)` 基础分（越小越相关，取负归一）+ 类别加权（现代教材 1.2 / 方书 1.1 / 医案 0.9，医案个体经验多）+ 命中实体覆盖率 |
| 去重 | 同一 `doc_id` 最多取 2 段；相邻 `seq` 且正文重叠 > 50% 的片段合并 |
| top-k | **5 段**，每段截到 **500 字**（超长片段取命中位置前后各 250 字，不用段首） |
| 空检索判定 | 查询有效字符 < 2、或最高分低于阈值、或命中的全是停用词 bigram → 返回空集，**不硬凑**（宁可说"未检索到"） |
| 已知局限 | bigram 的隐式 AND 允许"两个 bigram 都在但不相邻"的误命中（如「太阳」「阳明」都出现于不同句）。用命中覆盖率与片段内连续性检查过滤；不追求零误召，靠 top-5 排序与人工质量抽检兜 |

**2.1.1（可选，不进 2.1.0 验收）**：混合检索 = 词法 top-20 → 用用户已配置的 OpenAI 兼容 embedding 重排 top-5。失败自动退回纯词法。`retriever.py` 只留一个 `rerank()` 钩子，2.1.0 里它就是 `return candidates[:5]`。

### 13.6 提示词注入与上下文预算（最容易翻车的一节）

现状：系统提示 = `SYSTEM_BOUNDARY` + `SKILL.md` 正文（**34.8KB ≈ 1.86 万字符**，实测）+ `references/` 文件名清单 + PDF 选区摘录。再把 2500 字检索块塞进去，总长会到 2 万字以上——**模型会开始忽略中段内容**（lost in the middle），检索就白做了。

**必须同时做两件事**：

| 措施 | 内容 |
|---|---|
| ① 检索块位置 | 放在**最靠后**（紧贴用户问题之前），顺序：`边界 → SKILL 正文 → references 清单 → 检索块 → 选区摘录 → 用户问题`。这是长上下文中召回率最高的位置 |
| ② 总预算封顶 | 检索块 **≤ 2500 字**（5 段 × 500）；总提示长度超 **12000 字**时按序裁剪：references 清单 → SKILL 正文（截到 6000 字，保留开头框架与结尾禁用清单）→ 检索块降到 3 段。**永不裁掉检索块与选区摘录**（这两块是"本次回答的依据"） |

**检索块格式**（编号即出处索引，格式固定便于校验与前端解析）：

```
【本地教材检索结果】以下是本次提问前从用户本地教材库检索到的原文片段，按相关度排序。
引用规则：只能使用下列片段中的内容作答；句末标注片段编号（如 [1]）；没有依据的推论必须说明是推论。
若下列片段不足以回答，直接回答"本地教材中未检索到相关内容"，不要编造书名、篇名、条文号或页码。

[1] 《伤寒论》· 辨太阳病脉证并治上 · 第 12 条
太阳中风，阳浮而阴弱，阳浮者热自发，阴弱者汗自出……

[2] 《方剂学》（第 3 版）· 第一章 解表剂 · 辛温解表
桂枝汤：桂枝三两、芍药三两、生姜三两、大枣十二枚、甘草二两……
```

**两层防幻觉**（缺一不可）：

| 层 | 手段 |
|---|---|
| 提示层 | 上面那段"引用规则"，写死在 `citations.py` 常量里，不随 SKILL.md 变动 |
| 校验层 | 回答返回后正则抽 `\[(\d+)\]`，凡编号 > top-k 的一律**剥离**该编号（保留文字），并在 `meta.citation_warnings` 记录；出现"第 X 条""第 X 页"但检索结果里没有该字串时，前端在出处条上加一句「条文号未在检索结果中出现，请核对」——只提示，不篡改回答 |

> 为什么校验层不能"检查是否编造"：文本相似度判定误报率太高。只做**可判定的**（编号越界）与**可提示的**（条文号无出处），其余交给提示层。

### 13.7 出处呈现与定位

| 位置 | 呈现 |
|---|---|
| 回答气泡内 | `[1]` 渲染为可点 chip（灰色小标，hover 显示书名·篇名） |
| 回答下方 | 一行出处条：`[1] 《伤寒论》· 辨太阳病脉证并治上` `[2] 《方剂学》· 第一章` |
| 点开 | `<dialog>` 出处面板：书名 / 类目 / 篇名路径 / 原文片段（命中处高亮）/ 两个按钮——「在资料库中打开」（`os.startfile` 打开原 txt 或 pdf）/「复制原文」 |
| 定位精度 | txt：用 `char_start` 计算行号，打开后**尽力**滚动定位（外部程序不保证，故文案写"已打开原文件，定位可能需手动查找"）；pdf：有文本层则给页码，扫描件给"该文件为扫描件，无文本层" |

不做内建 txt 阅读器（2.1 范围外）；不做"边读 PDF 边看教材"的双栏（3.0 再议）。

### 13.8 索引的构建、体积与增量

**体积估算（实测推导，不是拍的）**：

| 项 | 数字 | 说明 |
|---|---|---|
| 语料 | 111M 字符 / 207MB | §13.2 |
| 片段数 | **约 25.2 万段**（500 字段长、60 重叠） | 111M ÷ 440 |
| 片段正文 `chunks.text` | **317MB**（UTF-8，中文 3 字节/字） | 必须存，出处面板要用原文 |
| FTS5 bigram 倒排 | **约 400–600MB**（111M 个 bigram posting + 约 2–3 百万词条词典） | 主要成本项；词条去重后不再线性膨胀 |
| 合计 | **≈ 0.7–0.9GB** | 磁盘预算必须提前告知用户 |

因此**索引范围做成三档**，首建索引前先弹预估（这一句必须诚实，否则用户看到 1GB 会以为有 bug）：

| 档 | 范围 | 语料 | 索引体积 | 适用 |
|---|---|---|---|---|
| **精简**（默认勾选） | 现代教材 9 部 + 大辞典 + 常用方书（按类目白名单，约 60 部） | ~30MB | **~130MB** | 日常辨证/方剂/中药问题 |
| **标准** | 全部 `中医教材/*.txt` | 207MB | ~850MB | 要查冷门古籍原文 |
| **自定义** | 用户勾类目（本草/方书/针灸/医案…） | — | 按比例 | — |

**构建流程**：

1. 后台任务（复用现有任务/轮询机制，不新增长连接）：`POST /api/library/index/build {scope}`
2. 逐文件：探测编码 → 解码 → 清洗 → 切片 → 批量写（每 2000 段一个事务）
3. 进度事件：`{total, done, current_file, chunks, bytes}`，设置页与资料库卡片显示「正在索引 137/724」
4. 可取消（写完当前文件即停，已写片段保留，下次续建）；失败单文件跳过并计入 `errors[]`，不中断整体
5. **增量**：`(path, mtime, size)` 未变则跳过；文件删除 → 级联删 chunks；新增 → 只索引新文件
6. **离开不阻塞**：构建期间问答照常（检索块为空则不注入，回答退回"无教材依据但通用解释"）

**性能目标**：标准档全量 < 8 分钟（单线程实测上限，允许 2 线程）；内存峰值 < 400MB；查询 p95 < 300ms。

### 13.9 设置与界面

| 位置 | 新增内容 |
|---|---|
| 设置 → AI 设置 | 新分组「教材检索」：开关（默认开，未挂载资料库时灰显并提示"先去 SKILL 管理挂载资料库"）+ 索引范围三选 + 索引状态行（`724 部 / 25.2 万段 / 850MB / 更新于 10-30 22:10`）+ 「重建索引」「清理索引」 |
| 设置 → SKILL 管理 | 资料库卡片（§6.5）第二行增加索引状态与「建立索引」按钮 |
| 阅读页 AI 面板 | 回答下方出处条；无检索时显式提示「未使用本地教材」（让用户知道这次回答没有依据，而不是以为有） |
| 首次进入阅读页提问题 | 若已挂载但未建索引 → 一条置顶提示「资料库未建索引，正在使用通用知识作答」+「去建立索引」（**不自动建**，1GB 磁盘不能替用户决定） |

### 13.10 里程碑（2.1，N0–N4）

| 阶段 | 内容 | 交付物 | 预估 |
|---|---|---|---|
| **N0 语料普查** | 写一次性脚本跑全库：编码分布、异常文件、`<篇名>` 覆盖率、类目分布、体积统计 | `_build/语料普查-2.1.md`（含"哪些文件不可索引"清单） | 0.5 天 |
| **N1 切片 + 索引库** | `extract.py` / `chunker.py` / `store.py`；bigram 切分函数（`seg(text)`）+ FTS5 schema；CLI 验收入口（`python -m reader_service.library build --dry-run`）。**开工第一步先跑一次 FTS5/bigram 冒烟**（本机 sqlite 3.53.1 已验证可用，但 PyInstaller 打的 sidecar 用的是自带 `_sqlite3.pyd`，须在冻结产物里再验一次） | 单测：切片可逆、编码、bigram 切分、增量；能建出索引并用 `MATCH '太阳 阳中 中风'` 召回正确片段 | 1.5 天 |
| **N2 检索与排序** | `retriever.py`：查询构造、同义词表、BM25 + 类目加权、top-k 截取 | golden set（30 问）top-3 命中率 ≥ 80% | 1 天 |
| **N3 注入与出处** | `build_system_prompt` 增 `retrieval` 段、预算裁剪、`citations.py` 校验、出处条与面板、`/api/assistant/ask` 返回 `citations[]` | e2e：三个验收问句全过 | 1.5 天 |
| **N4 设置、增量与发布** | 索引范围三选、进度、重建/清理、增量、`2.1.0` 打包与装机实测 | 安装包 + 索引耗时/体积实测报告 | 0.5 天 |
| 合计 | | | **≈5 天** |

依赖：N0 独立；N1 → N2 → N3 串行；N4 收口。**前置：2.0 已发版且 `library.json` 可用**（§13.13）。

### 13.11 测试与验收

| 层 | 内容 |
|---|---|
| Python 单测 | 切片：**可逆拼接**（同文件片段拼回 = 清洗后全文）、不切破句、`<篇名>` 变 heading、超长文件不丢段；编码：GB18030/UTF-8/UTF-8-BOM/截断多字节各一例；bigram：`seg('太阳中风') == '太阳 阳中 中风'`、单字/英文/标点边界；索引：增量（改 mtime 重索引、删文件级联、加文件）；**检索回归：2 字词「中风」「桂枝」「太阳」必须命中**（这条专门防有人把索引改回 trigram）、固定 30 问 top-3 命中率、空查询不返回、越界 `[n]` 被剥离；预算：超 12000 字时裁剪顺序正确且检索块保底 3 段 |
| JS 单测 | 出处条渲染（无 citations 时不渲染）、chip 点击取数、警告文案拼接 |
| 端到端 | ① 挂载 → 建精简索引 → 问「桂枝汤组成」→ 有 chip → 点开出原文；② 问「量子纠缠」→ "未检索到"且无 chip；③ 关闭检索开关 → 回答无出处条且提示"未使用本地教材"；④ 删除挂载目录 → 提问不崩、提示未建索引；⑤ 索引中提问 → 不阻塞、有"未使用教材"提示 |
| 质量（人工） | 30 问 golden set 逐条判定"引用是否真的支撑回答"，合格率 ≥ 80%；抽查 20 条出处，书名/篇名与实际文件一致率 100% |
| 性能 | 标准档建索引 < 8 分钟；查询 p95 < 300ms；内存 < 400MB；索引体积落在预估区间 ±20% |
| 装机 | 干净机：装 2.1 → 挂载资料库 → 建精简索引 → 三个验收问句全过 |

### 13.12 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 索引占 0.7–0.9GB，用户觉得"读书软件不该吃这么多盘" | 差评/卸载 | 默认精简档（~130MB）；建索引前显式弹预估；可随时清理，清理不动原文 |
| 古籍 OCR/录入错字导致召回差 | "明明书里有，却搜不到" | 同义词扩展 + bigram 字级匹配（错一字只废相邻两个 bigram，优于整词分词的"一错全错"）；出处面板标注"古籍文本可能存在讹误" |
| 模型忽略检索块，仍凭训练知识作答 | 检索形同虚设 | 检索块置于最末 + 预算封顶防"中段遗忘" + 提示层强制"只能使用片段内容" + 出处条可视（用户能看出没引用） |
| 检索块把模型带偏（片段断章取义） | 答案比不检索更差 | 片段带 heading 上下文（书名·篇名），不只给正文；top-5 不做 20 段堆砌；提示层允许"片段不足以回答" |
| 扫描版 PDF 无文本层 | 西医教材基本不可检索 | 2.1 明确不索引 PDF；设置页显示"该文件为扫描件，未索引"，不静默跳过 |
| 提示词里塞检索结果涉及版权顾虑 | 分发风险 | 教材不进安装包（2.0 已定），片段只发往用户自己配置的模型端点；不做任何云端缓存 |
| SKILL.md 1.86 万字挤占预算 | 检索块被裁 | 2.0 种子版 SKILL.md 裁到 ≤8000 字（§13.13 前置项）；超限按 §13.6 顺序裁剪 |

### 13.13 与 2.0 的接口契约（2.0 现在就要留的钩子）

2.0 完全不做检索，但下面 6 处必须按此留好，否则 2.1 要返工：

| # | 2.0 必须做到的 | 为什么 |
|---|---|---|
| 1 | `library.json` 结构固定：`{"<skill>": {"path": "...", "textbook_dirs": ["中医教材"], "added_at": "..."}}` | 2.1 直接读它找语料根，不再猜目录名 |
| 2 | `build_system_prompt(base, skill, section_excerpt, extra_sections: list[str] | None = None)` —— **第 4 参就位**，2.0 里恒传 `None` | 2.1 只加一个调用点，不改签名，不碰 3 处已有调用 |
| 3 | 设置 → AI 设置预留「教材检索」分组容器（2.0 里显示"需要安装 2.1 才有此功能"的灰条，或直接不渲染空组） | 2.1 只填内容，不改信息架构 |
| 4 | 种子版 `SKILL.md` 的措辞写成条件式：「**若提示词中提供了本地教材片段，则优先引用并标注编号**；若未提供，直接作答并说明依据为通用知识」 | 2.1 无需改 SKILL.md 文案即可生效；反之若 2.0 写成"必须先检索"，2.1 上线前全是幻觉 |
| 5 | 种子版 `SKILL.md` 正文裁到 **≤8000 字**（当前 1.86 万字 / 34.8KB） | 否则 2.1 的检索块必然被裁剪掉（§13.6） |
| 6 | 安全：**绝不 import / exec 用户数据目录里的 `scripts/*.py`**；`text_utils` 的逻辑抄进 `reader_service/library/extract.py` | 用户目录可写 = 可执行代码注入点 |

---

## 14. 修订记录与遗留项

### 14.1 本版修订（2026-10-30 第一轮评审落地）

| 项 | 第一版（待评审稿） | 本版（已定） |
|---|---|---|
| `yixue-zonghe` | 精简版 30–45MB 随包 + 外部知识库目录 | 历经三稿，**最终：认知层随包 5.4MB + 资料层可选挂载**（详见下一行） |
| PDF 文件夹 | 建议"只影响新导入" | **确认只影响新导入**；补齐迁移接口、后台进度、失败恢复、并发导入互斥（§5.3） |
| 词典 161MB | 建议随包、待实测 | **确认随包**；删掉"首启解压/下载"两条备选，只保留"安装耗时 > 3 分钟"这一条回退阈值 |
| 项目定位 | 只写了"Reader 是本体"一句 | 新增 **§0.1**：核心 / 插件 / 技能三层，四条硬约束，命名与 `identifier` 策略 |
| 设置接口 | `PUT /api/settings/library` 带 `migrate` | 拆为 `PUT`（只改路径）+ `POST …/migrate`（后台任务 + 状态轮询） |
| 里程碑 | ≈13.5 天 | **≈13.5 天**（M3 +0 含种子补种与资料库登记；M6 由"裁剪技能"改为"种子打包 + 引导"） |
| 技能随包策略（第二轮修订） | "完全不随包，首启引用开发机目录" | **"认知层随包 5.4MB + 资料层不随包"**；新增三种机器状态矩阵、`SKILL.md` 运行环境声明（防模型假装检索） |
| **2.1 教材检索（2026-10-30 立项）** | 只是 §6.5 末尾一行"2.1 做检索"的备忘 | **独立章节 §13**：语料实测（1.11 亿字 / 25.2 万段 / 索引 0.7–0.9GB）、切片与检索规则、提示词预算封顶、两层防幻觉、N0–N4 约 5 天、6 条 2.0 接口契约 |

### 14.2 本版明确关闭（不再讨论）

- ❌ Tauri 后端 Rust 重写（沿用 sidecar 路线，已有 702 个 pytest 护航）
- ❌ 插件开原生 Tauri 窗口（一律 Reader 内 `<dialog>` + 同源 iframe）
- ❌ 整个 2.3GB 技能随包（教材属资料层，AI 读不到，见 §6.3 事实一）
- ❌ "外部技能引用"`external.json`（全新机上无目录可引，与随包种子重复，已作废）
- ❌ 技能在线市场 / 自动下载
- ❌ 2.0 内改 `identifier`、exe 名、安装目录名（会破坏覆盖升级）

### 14.3 遗留项（不阻塞 2.0，登记备查）

| # | 事项 | 处置 |
|---|---|---|
| 1 | `learningtools-main/` 与 `408-guided-reader/` 是否合并为单一 git 仓库 | 2.0 只做**逻辑归一**（§0.1）；物理合并待 2.0 发版后单独评估 |
| 2 | `dailyread-skill/` 是否仍需随包 | 它不属于 yixue-zonghe、体积小；M6 打包时确认无引用即从 `binaries/` 移除 |
| 3 | `发布增量包.ps1` 不支持删除文件（缺 `removed[]`） | 2.0 用**全量安装包**交付；增量包补删除清单排到 2.0.1 |
| 4 | GitHub Release v1.3.1 未发布；旧 1.0.0 安装包 + 13GB `target/` 缓存未清 | 2.0 发 Release 时一并处理，现在不动 |
| 5 | 应用对外名改 Guided Reader 后，开始菜单/快捷方式仍为 LearningTools | 2.0 保持；彻底改名属 3.0（需换 identifier + 数据迁移） |
| 6 | 词典 161MB 若实测安装耗时超标 | 回退方案已写在 §6.2 注：随包 zip + 首启解压（体积不变、首启慢） |
| 7 | 教材资料层（1.37GB）的检索：让 AI 真正"翻教材" | **已立项为 2.1，方案见 §13**（不再是"待评估"）。2.0 只按 §13.13 留 6 处接口 |
| 8 | 用户想把 2.3GB 完整技能搬到新机 | 2.0 不提供迁移工具；手动复制目录后在设置里「挂载」（只登记路径）。装机验收只保证"没有资料也能用" |

---

## 15. 下一步（待你一句"开始"即执行）

1. **M0 备份**（§8.1 全流程）：`git tag v1.3.1` → 整包 zip → 数据冷备 → 归档 1.3.1 安装包 → 写 `restore-1.3.1.ps1` 并**实测回滚一次**。完成前不动任何业务代码。
2. 记录基线：`pytest` 702 项、`npm test` 的 19 项既有失败清单、三个 e2e 结果，写入 `_build/基线-1.3.1.md`。
3. 开分支 `release/2.0`，按 M1 → M6 推进；每个里程碑结束跑一次全量回归并对比基线。
4. 2.0 发版后按 **§13（N0–N4）** 推进 2.1。M1–M6 期间遇到 §13.13 的六处接口就顺手留好，不要等 2.1 再补——`build_system_prompt` 的第 4 个参数、`library.json` 的 `textbook_dirs`、SKILL.md 的条件式措辞，这三项事后补都要动一堆调用点。

---

## 16. 实施进度（实时更新）

> 每个里程碑完成即更新本节；"验证"列写实际跑出的数字，不写"应该没问题"。

| 里程碑 | 状态 | 实际产出 | 验证 |
|---|---|---|---|
| **M0 备份与基线** | ✅ 完成 | 备份 `E:/backup/LearningTools-2.0-M0-20261030-231911`（1203.4MB：源码包 644.9MB / state 39MB / blobs 154MB / 1.3.1 安装包 365.6MB）；`git tag v1.3.1` → `f06cf87`；`restore-1.3.1.ps1` + `回滚到1.3.1.bat`；`_build/基线-1.3.1.md`；`_build/m0_backup.py`（可重跑） | 回滚**实测**：写到临时目录后 `integrity_check=ok`，books/annotations/outline 计数与线上库完全一致；pytest 702 全绿；npm 160/19 |
| **M1 启动即 Reader** | ✅ 完成 | `index.html` 重写为启动页（拉起 sidecar → `location.replace` 到 READY 地址，失败可重试/新窗口打开）；`构建外壳.py` 退化为通用变换（Bootstrap + 主脚本 text/plain）；`lib.rs` 去掉 `GUIDED_READER_AUTOSTART` 开关；窗口标题 Guided Reader、1280×860、开始菜单目录 Guided Reader | `cargo check` 通过；dist 启动页生成（7.4KB）；`npm test` 的 dom-id-wiring 仍绿 |
| **M2 删除行间教学 + KP** | ✅ 完成 | 后端删 knowledge 包、learning 的 service/repository/display、teaching 的 3 个 inline 模块、三组路由、AI 问答两个分支；阅读进度独立为 `progress/reading.py` 并去掉掌握度耦合。前端删 `master-ui.js`(651) / `inline-ui.js`(287) / `inline-placement.js`(17)，app.js 去掉知识点面板、Master 侧栏、行间面板（−约 280 行），index.html 删面板。**净删除 10962 行**（43 文件，+170） | pytest **483 全绿**（702−219，删掉的都是被删功能的用例）；npm **154 通过 / 19 失败**（失败集与 1.3.1 基线逐项相同）；e2e `assistant-excerpt-menu` 通过；新增 `no-knowledge-map.test.js` 护栏（3 条） |
| **M3 插件体系 + 技能种子** | ✅ 完成 | `plugins/registry.py`（双根扫描 / manifest 校验 / 启停持久化 / 静态资源白名单 / zip+目录安装 / 回收站卸载 / 选区动作聚合）；`skills/seed.py`（整目录补种、缺件补齐、不覆盖用户改动、`restore_skill` 先建后挪进 `skills-trash`）；`skills/library.py`（资料库只登记不复制，`library.json` 带 `textbook_dirs`）；server 新增 9 条路由（`/api/plugins`、`/api/plugins/<id>/enabled`、`/api/plugins/install`、`DELETE /api/plugins/<id>`、`/api/selection-actions`、`/plugins/<id>/…`、`GET/PUT/DELETE /api/skills/<n>/library`、`POST /api/skills/<n>/restore`）；`plugin-actions.js`（清洗/排序/上下文/URL）+ `plugin-host.js`（dialog+iframe+postMessage）；index.html 去掉写死的「拼音/词典」，改插件容器 + 插件弹窗；`default-skills/yixue-zonghe/` 种子 4.97MB / 69 文件，SKILL.md **5201 字**（≤8000）含运行环境声明与条件式检索措辞 | pytest **558 全绿**（483→558，新增 75：注册表 34 / 资料库 11 / API 17 / 种子内容 6 / 补种 9，减 2 条旧用例改写）；npm **173 通过 / 19 失败**（失败集仍是基线那 19 条）；**真机 e2e**：sidecar 起后 `/api/plugins` 列出 bundled 插件 → 停用后 `/api/selection-actions` 空 → 启用恢复；zip 安装 pinyin 进 user 根、卸载进 `plugins-trash/20261031-002446-pinyin`、随包 dict 卸载返回 409；重启后 `dict enabled=true` 保持；`restore` 200 且旧版进 `skills-trash`；Edge 无头浏览器冒烟：`window.pluginHost` 就绪、选区行渲染「词典」按钮、弹窗标题「中医辞典」、iframe src 带 `?action=lookup&text=本虚标实&theme=light`、停用后按钮数 0、**控制台零报错**；§13.13 六处钩子 #1/#2/#4/#5 已落地（#3 归 M5，#6 由设计保证：从不 import 用户目录 scripts） |
| **M4 两插件改造** | ✅ 完成 | `plugins/dict/`（`plugin.json` + `icon.svg` + 由 `tools/dict/辞典.html` 派生的 `index.html`；词典检索逻辑一行未改，只加 4 处：图标路径、白天主题变量、`?text=` 走 `?q=` 同一路径、契约脚本 `lt-plugin-context` + 「在书中搜此词」回传）；`plugins/pinyin/`（重写：三种呈现 ruby/括号/上标 + 三种复制 ruby/上标/纯文本 + 19 条中医纠音内置 + 「追加纠音」只写 localStorage + 跟随 `?theme=`）；`_build/m4_plugins.py`（可重跑的同步脚本：161MB 数据、派生词典页、pinyin-pro.js，全部幂等）+ `_build/m4_dict_plugin.py`（派生器，带 4 项自检）；插件资源改为 `private, max-age=3600`（词典分块 JSON 重开不重下） | pytest **569 全绿**（+11 插件契约测试：manifest 合法、动作顺序 40/45、页面必须读 query 且监听 message、无外网兜底、目录内无白名单外后缀）；npm 173/19（同基线）；**浏览器实测**：选区行出现「词典」「拼音」两键 → 词典弹窗 1 条命中（「9 部 · 205,831 条 · 120ms」）→ 拼音弹窗 12 个 ruby，注音 `zú sān lǐ shì zú yáng míng wèi jīng de hé xué` 全对 → `theme=light` 生效、iframe 内 `data-theme=light`、Reader 控制台零报错 |
| **M5 设置重构 + 主题** | ✅ 完成 | `settings_store.py`（`settings.json` 原子写：主题 / 库根目录校验 / 引导标记；不塞 sqlite——首屏要同步读到）+ 10 条单测；`_build/m5_theme.py`：三个 CSS 共 **1139 处**硬编码色 → **569 个** `--t-<hex>` 令牌（`theme.css`），亮色=原值（1.x 观感零回归）、暗色=同色相亮度**保序反相**（`0.06+(1-l)*0.88`，不 clamp，纸—墨色阶在夜间不塌）；**低透明度暗色（阴影/蒙层）不迁移**——否则夜间 `rgba(0,0,0,.28)` 反相成白色光晕；PDF 页面/封面只按 `--page-dim`（0.75/0.9）压暗不反色；`--revert` 整链可回滚（实测回滚 1137 处后 git diff 仅剩语法格式差异，checkout 即净）；防闪：服务端把偏好注进 `<html data-theme-pref>`（`_html()` 直读 settings.json），`<head>` 内联脚本在所有样式表前解析，深色用户一帧白屏都没有；`theme.js`（归一化/解析/镜像/PUT/matchMedia 跟随，显式选择后不再跟随）11 条单测；设置重构为 **6 标签**（通用/插件/SKILL/AI 设置/快捷键/关于）+ ARIA tablist + 方向键导航；新增 `plugins-ui.js`（列表 / 启停 / 装 zip / 卸载 / window 型插件「打开」入口——工具栏在 1024px 下本来就溢出，实测后放弃内联按钮改设设置页入口）；SKILL 面板补：随包徽标 + 体积 + 资料库挂载/取消（含教材目录计数）+ 恢复内置；「关于」页读 `/api/about`；§13.13 钩子 **#3**（主题切换 `pushTheme` 重推 `lt-plugin-context`）落地，六钩子全部完成 | pytest **579 全绿**（569→579，+10 设置存储/路由测试；修复测试假 service 无 `paths` 导致 notes 冒烟崩溃）；npm **184 通过 / 19 失败**（+11 主题单测，失败集仍是基线 19 条）；**真机浏览器冒烟** `tests-e2e/theme-smoke.mjs` **23 项全过**：6 标签渲染无 undefined/NaN、关于页读到版本与目录、点深色后 `<html>` 属性与 localStorage 镜像同步、背景 `rgb(233,230,222)→rgb(46,43,33)` 实测变暗且文字比背景亮（可读性）、刷新后首屏直接深色（无白闪）、插件页 `data-theme=dark` 跟随、SKILL 资料库状态渲染、控制台零报错；截图核对设置 6 标签 / 插件深色 / 阅读页 PDF 只压暗 |
| **M6 引导 + 打包发布** | ✅ 完成 | 三步引导 `onboarding.js`（只在 `settings.onboarding.done` 非 true 时弹一次；Esc=跳过即完成；「自动检测」扫 4 个已知位置命中即预选；挂载只登记路径进 `library.json`，`Path.resolve(strict=True)` 归一物理路径）；换书库目录（§5.3）：`ManagedPaths.blob_override`，**读永远双根**（外部→默认 blobs，旧书照常）、**写落当前生效目录**，迁移是显式动作且同名跳过不覆盖；版本号 2.0.0 全链同步（tauri.conf / package.json / `__init__.py`）；PyInstaller spec `_include_tree` 打入 `default-skills/`（5MB）与 `plugins/`（161MB dict）；Tauri 资源 → `reader-service/_internal/`；NSIS offlineInstaller WebView2；dailyread-skill 移除（无引用）。**装机验收实测修出 2 个 frozen bug**：① `appbase.py`——`parents[3]` 在 onedir 下解析到 exe 目录而非 `_internal`，随包插件列表为空、种子缺失，改走 `sys._MEIPASS` 候选链；② 升级用户（已有书、无 onboarding 字段）会被弹引导，改为「有书即视为完成」。安装链陷阱实测记录：NSIS 会把 `/D` 之外的记忆目录（`HKCU\SOFTWARE\<厂商>\<产品>` 默认值）无条件恢复为 INSTDIR，且**同名版本重装会静默跳过**——升级必须清记忆键或换版本号；装完后真实路径是 `reader-service\_internal\{default-skills,plugins}` | pytest **588 全绿**（579→587：+6 书库目录、+8 appbase、+1 引导回归、−6 旧用例改写）；npm 187/19（基线不变）；frozen 验收：`/api/about` version=2.0.0+frozen=true、`/api/plugins` 列出 dict+pinyin、种子 yixue-zonghe 落地、静态资源全 200、真机数据（中医内科学）升级后原样保留；**全新装 E 盘验收目录**与**真机升级 D:\LearningTools（1.3.1→2.0.0）**均通过，控制台数据目录含 5 段迁移备份自动生成。产物：`成品/安装版/LearningTools-2.0.0-安装包.exe` **322.8MB** + `LearningTools-2.0.0-更新包.zip` **322.9MB**（全量构建 3m28s–4m29s；API Key 体检通过）。**干净虚拟机装完即用验收**本机无法执行，待用户在干净环境复核。**交付后真机回归（10-31 用户报告）修出第 3 个 bug**：③ WebView2 运行时升级到 156 后，启动器主窗口整页跳转（`tauri.localhost → http://localhost:PORT`）被标记 `Sec-Fetch-Site: cross-site + Dest: document`，遭本地 CSRF 网关拒绝，应用启动整窗显示 `UNTRUSTED_LOCAL_ORIGIN`（1.3.1 同逻辑，纯运行时环境演变触发）；修复为豁免扩展到 `document` 的只读 GET/HEAD（表单 POST 与 `dest=empty` fetch 依旧拦截），+1 回归测试，提交 `d2089ba`；实锤方式：curl 模拟四种请求形态对照 403/200，真机复核 200/200/403/403 全部符合预期 |
| **交付后真机反馈修复（10-31，7 项）** | ✅ 完成 | 用户真机反馈 7 项，逐项修复并各自提交：① PDF 选取文字看不到选取状态 + ② 下划线/高亮/区域高亮全部不可见——同一根因：M5 令牌化后 `rgb(var(--t-x)/α%)` 中 var 展开为 hex → `rgb(#417ed3 / 20%)` 在 computed-value 阶段整体作废 → 背景回落透明（亮色 100% 下看不出来，暗色+150% 下复现实锤）；改用 `color-mix(in srgb, var(--t-x) α%, transparent)`（Chromium 111+），`_build/m5_theme.py` 新增 `--fix-alpha` 就地改写历史产物，三个 CSS 共修正 102 处，提交 `16d486b`；③ 辞典弹窗左右比例失衡（左 55%）——`#side{flex:0 0 272px}` 被 flex 项 `min-width:auto` 陷阱撑爆（命中行 min-content 535px），加 `min-width:0`，提交 `6289b4b`；④ 移除工具栏「本章知识点 / KP·重试」入口（screens.js `createChapterEntry` 置空实现 + 删 CSS/测试），同 `6289b4b`；⑤ 笔记导出 MD 无效——Tauri/WebView2 不接管 `<a download>`+blob（静默丢弃），桌面外壳（检测 `__TAURI_INTERNALS__`）请求加 `save=1` 由本地服务直写系统「下载」文件夹（重名自动序号）返回 JSON 路径，浏览器保持 blob 锚点，提交 `6e5fc6e`；⑥ 全书批注面板整条内容点击跳转——新增纯函数 `annotationJumpTarget`（rect 上沿优先/quads 取最小 y/无定位落页首）+ 面板整条 item 可点击（含 Tab+Enter 键盘）+ 跳后多轮重试 flash，提交 `ee38e5d`；⑦ 问 AI「所选内容存为批注」Markdown 自动补齐——选区截断导致表格缺表头/围栏未闭合/`**` 反引号 `$$` 失配，新增 `markdown_repair.py` 保守补齐（不增删词语，只补最少语法），挂接 `SavedExplanationService._resolve_excerpt`，注意：首版表格修复存在**索引推进 bug 导致死循环**（插入 2 行后未跳过本块，反复修复同一块内存爆涨），已修复并加回归用例 | pytest 全量**通过 0 失败**（收集 610：+12 markdown_repair、+1 摘录补齐集成、+5 annotationJumpTarget JS 纯函数、+1 e2e fix-panel-jump 三种触发方式全过）；npm 190/19（失败集与基线完全一致）；真机 e2e：暗色+150% 拖选 quad 非透明、菜单弹出、控制台零报错；辞典 980 弹窗 side 恢复 272px |
| **2.1 教材检索** | ⬜ 未开始（2.0 发版后） | | |
| **交付后真机反馈修复二（10-31，3 项）** | ✅ 完成 | 导出弹窗自选保存位置——用户「下载」文件夹被移走致 save=1 通道报错；新增 `native_dialog.py`（ctypes 调 comdlg32 GetSaveFileNameW，零依赖）弹系统「另存为」，`dialog=1` 选定直写/取消返回 cancelled/环境不支持回落下载文件夹，提交 `3ea47f0`；问AI默认技能随设置勾选——`selected()` 无记忆时回落首个启用的随包技能（不再退内置助手），builtin 判定移除 is_default 混入，`3ea47f0` 同批 + `fb56195`；「所选内容存为笔记」不可用真因——**右键按下会把选区塌陷成光标**，右键点在选区外时工具栏静默不弹（真机库只读验证：AI_SAVED 从无一条 excerpt），修复为 pointerup 暂存有效选区 + contextmenu 暂存恢复 + 右键在选区外锚定选区弹栏，`fb56195`/`aff1086`；附带 PDF 便签注释剥除 markdown 符号（`markdown_to_plain_text`，应用内卡片仍 markdown 渲染） | pytest 全绿（+2 pdf_sync、+5 skill-default JS）；npm 195/19（基线一致）；真机复现→修复→复验闭环（ASK=1 两次真实提问，摘录入库 payload.answer 为修复后 markdown，默认技能=yixue-zonghe）；第 8 次打包重装 D 盘，frozen 静态资源字符串核验三项修复全在、批注跳转 e2e ALL PASS |
| **交付后真机反馈修复三（10-31，摘录格式）** | ✅ 完成 | 用户反馈：AI 回答片段保存为笔记后成纯文本（整条回答保存是对的）。根因：渲染时源码区间映射只标注可见文本节点，按区间回原文切片时 `**`/`#`/`|` 等结构性标记不在任何文本节点里，摘录天然丢格式。修复为**从渲染 DOM 反向序列化回 markdown**——`assistant-render.js` 新增 `selectionToMarkdown(range, bubble)`（对 `range.cloneContents()` 反向序列化：标题/粗斜删/行内代码/列表/引用/表格/围栏，表格缺表头补空表头），随 `excerpt.markdown` 提交；后端 `saved_explanations` 接受可选 markdown 字段（形状不对静默回落纯文本切片），**span 切片与可见文本的一致性校验保留**（保证摘录确实来自原回答），通过后优先存 markdown 版本；面板列表预览新增 `excerptPreviewText` 剥标记显示纯文本，卡片/笔记面板仍 `renderAssistantAnswer` 完整渲染；PDF 便签写回通道不变（`markdown_to_plain_text`，PDF 便签本身不支持排版）。提交 `44561b7` | pytest 全绿（+1 摘录 markdown 用例）；npm 221 测试 202 过/19 败（失败集=基线，新增 7 用例全过）；e2e 全链路 ALL PASS（真实提问→程序化选区→序列化诊断还原出粗体/引用/完整表格→保存→真机库摘录含 `**`→PDF 页徽章开卡→卡片渲染出 strong/list），验证写入的 2 条测试摘录已按内容签名精确删除；第 9 次打包重装 D 盘 |

| **交付后真机反馈修复四（10-31，回写语义+右键菜单）** | ✅ 完成 | 用户确认回写语义：批注改/删后再回写 PDF **会同步**——`sync_annotations_to_pdf` 取库内全量批注 → `write_annotations` 先删所有 `guided-reader-` 标记注释再逐条重建（全量镜像），删除→消失、修改→同 id 替换、外部注释不动；auto 模式增删改后自动写、manual 需手动「立即写回 PDF」/Ctrl+S；AI 笔记不可编辑只能删了重存。顺带修复验证中发现的真 bug：`/Popup` 弹出窗独立且无 `/NM`，原逻辑只删父注释 → 每轮回写残留一个孤儿空框（实测线性膨胀），`_remove_reader_annotations` 现同步清除父已删/父不在本页的 `/Popup`（第三方弹窗不动，removed 计数语义不变），测试 +2。另按用户截图反馈**禁用 WebView2 原生右键菜单**（`1187ddd`）：`index.html` 在 window 冒泡阶段兜底 `preventDefault`（业务自定义菜单先执行完才轮到兜底，不污染 `defaultPrevented` 信号），输入控件保留系统菜单；启动页经 `构建外壳.py` 同步（注意发布脚本不会自动跑它，需手动重建 `app/dist/index.html`）。e2e：`verify-context-menu.mjs` 两断言 PASS、`diag-excerpt` 全链路复跑 ALL PASS；pytest 全量绿。第 10 次打包重装 D 盘，应用已重启 |
