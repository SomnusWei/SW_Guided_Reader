# 笔记工具（Markdown 笔记本）方案

> 状态：三点决策已确认，开发中（M1→M4）
> 目标版本：1.3.0
> 编写日期：2026-10-30

---

## 一、需求与范围

在 Reader 阅读界面里，从工具栏入口唤起一个**弹窗式笔记工具**，支持：

| 能力 | 说明 |
| --- | --- |
| Markdown 输入 | 纯 Markdown 文本编辑，支持实时预览（复用现有 marked + KaTeX 渲染） |
| 保存 | 显式保存（按钮 / `Ctrl+S`）+ 自动保存（停止输入后 1.5 秒） |
| 查看历史 | 按更新时间倒序的笔记列表，支持关键词搜索、按教材筛选 |
| 增 / 改 / 删 | 新建、编辑、删除（删除需二次确认） |
| 导出 md | 单条导出为 `.md` 文件；另提供「导出全部」合并成一份 |

**明确不做**（一期）：富文本所见即所得、图片/附件、云同步、把笔记回注 PDF、多条笔记版本快照。

---

## 二、现状盘点（可直接复用的既有能力）

| 需要的东西 | 已有实现 | 复用方式 |
| --- | --- | --- |
| 弹窗容器 | Tauri `open_reader_window()`：`WebviewWindowBuilder`（`app/src-tauri/src/lib.rs:379`） | 照抄范式新增 `open_notes` 命令，开独立小窗 |
| Markdown 渲染 | `static/assistant-render.js`：`renderAssistantAnswer(el, md)`，marked + DOMPurify + KaTeX，禁 `a`/`img` | 预览区直接调用 |
| 导出下载 | `static/export-md.js`：`filenameFromDisposition()` + Blob 下载 | 复用文件名解析与下载逻辑 |
| 服务端导出 | `MarkdownExporter`（`src/reader_service/export_md.py`） | 新增 `note` / `notes` scope |
| 数据访问 | `Database.connect()`、迁移表 `MIGRATIONS`（当前到 31） | 新增迁移 32 |
| 路由 | `server.py` 内 `parsed.path` 正则分支（如 `_REVISION_ASSISTANT_HISTORY`） | 新增 `_NOTES` / `_NOTE_ITEM` / `_NOTE_EXPORT` |
| 静态资源 | 规则白名单（顶层 + js/mjs/css/html/svg/png/woff2/json 后缀 + 文件存在） | **新增 `notes.html` / `notes-ui.js` 无需登记**，自动可服务 |
| token 鉴权 | Reader 页从 `?token=` 写入 `sessionStorage` 并带 `X-Reader-Token` 头 | `notes.html` 与 Reader 同源同法，launch cookie 也自动携带 |
| 提示 | 页内状态行（「已保存 HH:MM」/ 错误条） | 独立窗口自带状态区，不依赖 Reader 的 `announce` |

---

## 三、入口与弹窗实现（平台侧栏）

**入口就是平台外壳（`index.html`）的侧栏**，与 Guided Reader / OneNote 拼音标注 / 中医辞典并列：

```
┌─ 平台侧栏 ─────────┐
│ 学 学习工具集        │
│ [搜索工具  Ctrl K]  │
│ 阅读学习            │
│   ▸ Guided Reader  │
│   ▸ 📝 笔记         │  ← 新增 TOOLS 项
│ 中医内容            │
│   ▸ OneNote 拼音标注│
│ 查检工具            │
│   ▸ 中医辞典        │
└────────────────────┘
```

### 三个关键架构事实（已核实）

1. 侧栏条目来自外壳 `index.html` 的 `const TOOLS` 数组，按 `category` 分组，点击 `openTool(id)` 后在**主区 iframe** 打开 `tools/*.html`
2. Reader 不是 iframe——它是**独立 Tauri 窗口**（label `"reader"`），服务地址由 Rust 命令 `ensure_reader_running()` 管理，页面由 reader-service 在 `http://127.0.0.1:<port>` 提供
3. Rust 侧已有 `open_reader_window()` 的 `WebviewWindowBuilder` 范式，新增一个开小窗的命令成本极低

### 交互流程

点击侧栏「笔记」→ **不开主区 iframe，直接弹独立小窗**：

```
Tauri 模式（安装版，主路径）：
  侧栏点击 → 外壳 openTool 分支（tool.popup）→ invoke('open_notes')
    → Rust: ensure_reader_running() 拿 READY URL
    → WebviewWindowBuilder(label "notes", url + "/notes.html")
      · 尺寸 520×720（竖版，适合放屏幕一侧陪读）
      · min 380×480，可最大化
      · 已存在则 show + unminimize + set_focus（与 open_reader_window 同法）

网页回退模式（开发期 file:// / serve.py）：
  侧栏点击 → fetch /api/reader 拿 url → window.open(url + '/notes.html',
  'notes', 'width=520,height=720')；服务未运行时提示先启动 Reader
```

- 弹窗是**独立窗口**，与 Reader 窗口并排——这正是"阅读时用笔记"的形态：左边教材、右边笔记，互不遮挡
- 弹窗内容 `notes.html` 由 **reader-service 自身提供**（`static/notes.html` + `static/notes-ui.js`）：与 Reader 同源，launch cookie 自动携带，鉴权零成本；端口/地址永远正确（不用外壳猜）
- `TOOLS` 数组加一项 `{id:'notes', name:'笔记', category:'阅读学习', popup:true, …}`；外壳 `openTool()` 开头加一个分支：`if (t.popup) return openNotesPopup(t)`（Tauri invoke / web window.open），侧栏高亮、搜索、`#/t/notes` 直达全部自动获得
- 可选增强（二期）：Reader 顶栏 `···` 菜单加"笔记"按钮，同源 `window.open` 打开同一个弹窗（体验一致）

---

## 四、数据模型（迁移 32）

新表 `notes`，**全局笔记本**，但记录来源教材以便追溯：

```sql
CREATE TABLE notes (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL DEFAULT '未命名笔记',
    body                    TEXT NOT NULL DEFAULT '',      -- Markdown 原文
    excerpt                 TEXT NOT NULL DEFAULT '',      -- 纯文本首段摘要，列表用
    book_source_revision_id TEXT REFERENCES book_source_revisions(id) ON DELETE SET NULL,
    page_index              INTEGER,                        -- 创建时的 PDF 页码（0 基）
    book_title_snapshot     TEXT,                           -- 教材标题快照，书删了仍可追溯
    pinned                  INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX notes_updated_idx ON notes(updated_at DESC);
CREATE INDEX notes_book_idx ON notes(book_source_revision_id);
```

设计取舍：

- **`ON DELETE SET NULL`** 而不是 `CASCADE`：删教材不该连带删掉用户手写的笔记；配合 `book_title_snapshot` 保留"这本笔记写自哪本书"的记忆
- **`excerpt`** 冗余一列：列表页展示首行摘要时不必把整篇正文读出来
- 一期搜索用 `LIKE`（标题 + 正文 + 摘要），数据量到万级也不慢；**不引入 FTS5**，避免分词与迁移复杂度
- 不做软删除：删除即物理删除，删除前弹确认（与全书批注一致）

---

## 五、HTTP API

全部走现有 `_authorized()` token 鉴权，错误格式沿用 `{"error": "中文原因"}`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/notes?query=&book=&limit=50&offset=0` | 列表（按 `pinned` 降序，再按 `updated_at` 降序），返回 `{notes, total}` |
| POST | `/api/notes` | 新建，body：`{title?, body?, book_source_revision_id?, page_index?}` → `{note}` |
| GET | `/api/notes/{id}` | 单条详情 → `{note}` |
| PATCH | `/api/notes/{id}` | 改 `{title?, body?, pinned?}` → `{note}`；只传变化的字段 |
| DELETE | `/api/notes/{id}` | 删除 → 204；不存在 404 |
| GET | `/api/notes/{id}/export` | 单条导出，`text/markdown; charset=utf-8` + `Content-Disposition: attachment` |
| GET | `/api/notes/export` | 全部导出（按更新时间分节合并成一个 md） |

校验规则（`NotesService` 内集中处理）：

- `title`：去空白后 ≤ 120 字；为空时回退为正文首个非空行（截 40 字），仍为空则「未命名笔记」
- `body`：≤ 200,000 字符，超出 400
- `page_index`：非负整数或 `null`
- 更新时 `updated_at` 一律刷新；`created_at` 不变

---

## 六、后端落地清单

| 文件 | 动作 |
| --- | --- |
| `src/reader_service/notes_schema.py` | `NOTES_SCHEMA` 常量（**独立模块**，避免 `library.database` 迁移期循环导入，做法同 `assistant_history_schema.py`） |
| `src/reader_service/notes.py` | `NotesService`：`list / create / get / update / delete / export_one / export_all`，内部 `threading.Lock`，SQL 全部参数化（分层位置同 `memory.py` + `memory_schema.py`） |
| `src/reader_service/library/database.py` | `MIGRATIONS = (..., (32, NOTES_SCHEMA))` |
| `src/reader_service/server.py` | 三个正则常量 + GET/POST/PATCH/DELETE 分支；`handler_factory` 增加 `notes` 参数 |
| `src/reader_service/__main__.py` | `notes=NotesService(service.database)` 装配 |
| `src/reader_service/export_md.py` | 新增 scope `note` / `notes`，复用现有 `Content-Disposition` 生成逻辑 |

导出文件名规则（与现有导出一致）：

- 单条：`笔记_{标题}_{YYYYMMDD}.md`（标题里的 `/ \ : * ? " < > |` 替换为 `_`）
- 全部：`笔记本_{YYYYMMDD}.md`
- 文件内容：`# 标题` + 来源信息行（教材 · 页码 · 更新时间）+ 正文原文（不二次加工 Markdown）

---

## 七、前端落地清单

### 新增 `static/notes.html` + `static/notes-ui.js`（reader-service 提供）

独立小窗页面（不是 Reader 内的 `<dialog>`），由 Tauri `open_notes` 或 `window.open` 打开。竖版布局，窄窗单栏、宽窗双栏：

```
┌─ 📝 笔记 ────────────────── [+ 新建] [导出全部] ─┐
│ 搜索框…                                          │
├──────────────────────────────────────────────────┤
│ ● 置顶                                            │
│   ★ 错题本·计组          2026-10-30              │
│ ● 今天                                            │
│   生脉散方义整理          12:04 · 中医基础 · P42  │
│   总线时序易错点          11:20 · 计组 · P118     │
├──────────────────────────────────────────────────┤
│ 标题：生脉散方义整理                               │
│ [编辑] [预览] [分栏]                              │
│ ┌──────────────────────────────────────────────┐ │
│ │ # 生脉散                                     │ │
│ │ - 人参 麦冬 五味子…                           │ │
│ └──────────────────────────────────────────────┘ │
│ 已保存 12:04                    [删除] [导出 MD]  │
└──────────────────────────────────────────────────┘
```

- 宽度 ≥ 720px 时左右双栏（列表 220px + 编辑区）；窄窗（默认 520px）上下堆叠，`笔记列表 / 编辑` 两个标签页切换
- **鉴权**：页面加载时从 `?token=` 取值写入 `sessionStorage`（与 `app.js` 同法），请求统一带 `X-Reader-Token`；launch cookie 同源自动携带，双保险
- **列表**：按「置顶 / 今天 / 昨天 / 本周 / 更早」分组；每行显示标题、摘要、来源（教材 · 页码）、更新时间；点击加载到编辑区
- **编辑器**：标题输入 + `<textarea>`；`编辑 / 预览 / 分栏` 三态，预览调用 `renderAssistantAnswer`
- **保存**：`Ctrl+S` 立即保存（页面级，无需注册快捷键系统）；停止输入 1.5 秒自动保存；状态行显示「已保存 HH:MM」或可点击重试的错误条
- **脏状态**：切换笔记 / 关窗（`beforeunload`）前，若有未保存内容先 `flush`，失败则 `confirm`
- **IME 保护**：`keydown` 判断 `event.isComposing`，避免中文输入法选词时误触
- **新建**：本地空白草稿（不立即写库），首次保存才 `POST`，避免产生空笔记
- **删除**：`confirm` 后 `DELETE`，从列表移除
- **导出**：单条走 `/api/notes/{id}/export`；全部走 `/api/notes/export`（Blob 下载）

### 来源关联（一期从简）

一期在编辑区顶部提供可选的「关联教材」下拉（数据来自 `GET /api/books`）+ 页码输入，手动选择；二期再通过 `BroadcastChannel` 让 Reader 窗口自动广播当前书与页码，实现"打开即带入"。

### 既有文件改动

| 文件 | 改动 |
| --- | --- |
| `index.html`（平台外壳） | `TOOLS` 数组加 `{id:'notes', name:'笔记', category:'阅读学习', popup:true, desc, tags, icon}`；`openTool()` 开头加 popup 分支：Tauri 下 `invoke('open_notes')`，网页回退下 `fetch('/api/reader')` 后 `window.open` |
| `app/src-tauri/src/lib.rs` | 新增 `#[tauri::command] open_notes`：`ensure_reader_running()` → `WebviewWindowBuilder(label "notes", url + "/notes.html")`，520×720 / min 380×480，已存在则 show+focus；注册进 `generate_handler!` |
| `static/shortcuts-ui.js`、`server.py _SHORTCUT_ACTIONS` | **一期不改**（独立窗口不经过 Reader 快捷键系统）；二期做 Reader 内入口时一并加 `notes` 动作 |

> 注意：外壳 `index.html` 与 Rust 在 Tauri 主程序/资源里，**不在增量包的映射范围内**（增量包只覆盖 `reader-service/_internal/`）。因此本功能完工后需发布**完整 1.3.0 安装包**；只有后端与 `static/notes.*` 部分可先行用增量包在开发机上验证。

---

## 八、安全与隐私

- 笔记只写本地 `state.sqlite3`，不经过 AI、不外发、不进入任何导出给第三方的链路
- 预览走现有 DOMPurify 白名单（禁 `script/style/iframe/object/embed/img/a`、禁 `href/src`），用户粘贴的 HTML 不会执行
- 若二期需要图片，必须新增本地存储目录 + 受控静态路由，不在本期承诺
- 导出为浏览器下载，不写入任意路径

---

## 九、测试计划

**Python（新增 `tests/test_notes.py`）**

1. 建表：迁移 31 → 32 升级后旧数据完好，`notes` 表存在
2. 新建：标题为空时回退为正文首行；正文超长 400
3. 更新：只改 `body` 不动 `title`；`updated_at` 刷新
4. 删除：删除后 `get` 抛 `LookupError`（404）
5. 列表：关键词命中标题/正文；按 `updated_at` 倒序；`pinned` 优先
6. 来源：删除教材后笔记仍在，`book_source_revision_id` 置空、`book_title_snapshot` 保留
7. 导出：单条与全部，`Content-Disposition` 文件名含日期、正文原文无损

**前端**：`node --check notes-ui.js`；手工验收清单——侧栏点击弹窗打开/新建/保存/关窗重开仍在/删除/导出/双栏与窄窗单栏切换/`Ctrl+Shift+R` 强刷后仍正常

**回归**：全量 `pytest`（当前 182 项）保持全绿；`tauri build` 通过

---

## 十、里程碑

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| M1 | 迁移 32 + `NotesService` + 七个接口 + 测试 | `pytest` 全绿，curl 打通 CRUD |
| M2 | `notes.html` + `notes-ui.js`（列表 + 编辑 + 保存 + 删除 + 导出） | 浏览器直开 `notes.html?token=…` 完整走通 |
| M3 | 外壳 `TOOLS` 项 + `openTool` popup 分支 + Rust `open_notes` 命令 | 安装版侧栏点击弹出笔记小窗，Reader 并排可用 |
| M4 | 全量回归 + **完整 1.3.0 安装包** + README/发布说明更新 | 安装包验证通过、真实库迁移无损、GitHub 发布 |

---

## 十一、风险与对策

| 风险 | 对策 |
| --- | --- |
| 外壳与 Rust 改动不在增量包映射范围 | 本功能发布走完整安装包；开发期用增量包先验后端与 notes 页面 |
| 中文输入法在 textarea 里与 `Ctrl+S`/`Esc` 冲突 | `event.isComposing` 早退；组合键仅在有修饰键时生效 |
| 自动保存与关窗竞态，导致最后一段丢失 | `beforeunload` 前 `flush` 未完成的保存；保存中显示状态并禁用关闭路径 |
| 超长笔记预览卡顿 | 预览截断到 50,000 字符（沿用 `assistant-render.js` 现有上限） |
| 网页回退模式下 `window.open` 被拦截 | 弹窗打开失败时在页面显示"点击手动打开"的直链兜底 |
| 迁移 32 在真实用户库上执行失败 | 与迁移 30/31 一致：建新表 + 索引，不触碰既有表，失败可回滚（备份目录已存在） |

---

## 十二、已确认的三点决策（2026-10-30）

1. **笔记归属**：**全局笔记本 + 手动关联教材页码**。列表默认展示全部笔记；每条笔记可（可选）关联一本教材与页码，用于追溯来源，不按当前书过滤。
2. **"查看历史"的含义**：**历史笔记列表**。按「置顶 / 今天 / 昨天 / 本周 / 更早」分组 + 关键词搜索，**不做**每条笔记的逐次版本快照（无版本表、无 diff）。
3. **导出范围**：**单条 + 全部**。单条导出当前笔记；「导出全部」按更新时间倒序合并成一份 Markdown。

据此进入 M1→M4 实施。
