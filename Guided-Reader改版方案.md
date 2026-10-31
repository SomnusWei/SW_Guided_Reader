# Guided Reader 改版方案

> 基于项目 `1433223-afk/408-guided-reader`（本地 408 考研 PDF 引导阅读器）的二次改造计划。
> 目标：从"408 专用"改造为"任意已 OCR 教材通用"的个人学习阅读器，可选集成 learningtools。
> 编写日期：2026-10-28 ｜ 基线版本：main 分支（guided-reader 0.1.0，2026-10-23 最后提交）

---

## 1. 改版目标

| # | 目标 | 说明 |
|---|------|------|
| G1 | 教材通用化 | 适配任意已 OCR（或可 OCR）的 PDF 教材，不再绑定王道 408 系列。首批验收对象：靳三针法、经络腧穴学、类经、针灸大成、中医内科学 |
| G2 | 批注能力 | 支持文本框、下划线、高亮、区域高亮、AI 问答批注五类，完整增改删；写回 PDF 三模式可配（关闭/手动导出/**自动写回**），支持直接修改源文件 |
| G3 | 大纲/目录修复 | 解析 PDF 自带书签大纲，目录条目可点击并正确跳转到对应页 |
| G4 | 问答导出 | Assistant 提问回答（及保存的解释、Master 对话）可导出为本地 Markdown 文件 |
| G5 | AI 配置扩展 | 内置国内主流大模型 API 支持，可配置任一家的 API Key |
| G6 | OCR 替换 | 移除内置 rapidocr/onnxruntime 依赖，改为调用本机 Umi-OCR 的 HTTP 服务 |
| G7 | 平台定位 | 仅 Windows 本地端；集成到 learningtools（用户本人仓库，提供私有下载，可细化对接） |
| G8 | 功能裁剪 | 移除习题（Practice）全部功能与数据 |
| G9 | AI Skill 系统 | AI 提问引入技能（Skill）机制：默认内置 `yixue-zonghe`（来自 SomnusWei/Dailyread），提问时先选择 skill 再走配置的 API Key；支持通过配置添加新 skill |

---

## 2. 改动总览

| 现有模块 | 处置 | 改动要点 |
|----------|------|----------|
| `foundation/`（PDF 渲染、文字锚点） | ✅ 保留 | 核心资产，批注/选区/跳转都依赖它 |
| `library/`（书库、上传） | ✅ 保留 | 放开对教材类型的隐含假设 |
| `annotation/`（批注） | 🔧 改造 | 现仅支持文字选区高亮（YELLOW/GREEN/BLUE），扩展为四类批注 |
| `outline/`（大纲） | 🔧 改造 | 增加"直接读取 PDF 书签"通道，修复点击跳转 |
| `assistant/` + `agent_runtime/`（AI 问答） | 🔧 改造 | provider 注册表化 + 导出 Markdown + Skill 系统（G9） |
| `teaching/`（导读、行间教学） | 🔧 弱化保留 | 保留骨架；内容生成依赖教材准备流水线，通用教材可后置 |
| `knowledge/` + `learning/`（知识点、Master） | 🔧 弱化保留 | Master 对话保留并支持导出；知识点自动构建对通用教材降级为可选 |
| `memory.py` / Learning Memory | ✅ 保留 | 学习记忆收纳不变 |
| `visual_region.py`（区域框选） | ♻️ 复用 | 区域高亮批注直接复用其框选交互与坐标逻辑 |
| `practice_*.py`（6 个文件）+ 前端 Practice | ❌ 移除 | 连同路由、数据库表、前端 UI 一起清理 |
| rapidocr / onnxruntime 依赖 | ❌ 移除 | 换成 Umi-OCR HTTP 适配层 |
| OCR 流水线（`jobs/`、tools） | 🔧 改造 | 页级按需识别改为调 Umi-OCR；整本预识别走 Umi-OCR 文档接口 |

---

## 3. 模块方案

### M1 教材通用化（对应 G1）

**现状**：书库本身按"上传 PDF → blob 存储（SHA-256）→ revision 管理"设计，天然与教材类型无关；耦合点在习题（王道专属）、知识点流水线、以及 README/文案层面的 408 假设。

**方案**：

1. 上传入口放开：任意 PDF 可导入，导入时探测三层信息并标记——
   - 是否含文字层（pypdf 提取验证）→ 有文字层则跳过 OCR；
   - 是否含书签大纲（`reader.outline`）→ 决定 G3 走哪条通道；
   - 页面尺寸/方向异常（竖排古籍常见）→ 提示用户。
2. 移除所有"王道 / 408 / 计算机组成原理"的代码级引用与前端文案；产品名建议改为 **Guided Reader（通用版）**。
3. 知识点（KnowledgePoint）自动构建流水线降级为**可选增强**：不预置任何教材知识库，用户可手动创建知识点、将选中内容/批注/AI 回答挂到知识点下。未建知识点的教材，Assistant 与批注功能完整可用。
4. 教材准备流水线（原 408 的 OCR/目录/证据修复 tools）重构为"导入向导"：上传 → 检测 → （需要时）Umi-OCR 逐页/整本识别 → 大纲确认 → 可读。

**中医教材注意点**（验收对象特性）：
- 《针灸大成》《类经》为繁体竖排扫描件：Umi-OCR 需切换繁体中文模型库并开启竖排识别；验收标准单列（见 §7）。
- 《经络腧穴学》含大量经络图、穴位图：区域高亮与"框选提问"（复用 visual_region）是主要交互，不要求 OCR 覆盖图内文字。

### M2 批注系统（对应 G2）

**现状**：`AnnotationService` 已有文字锚定批注（`create_text`：基于 foundation 的 `resolve_text_selection` 生成 quads + quote + 上下文，防漂移），样式仅 YELLOW/GREEN/BLUE 高亮。

**方案**：批注类型从 1 种扩展为 4 种，统一存入 `annotations` 表（新增 `kind` 字段）。

| 类型 | 锚定方式 | 实现要点 |
|------|----------|----------|
| 高亮（现有增强） | 文字选区 → quads | 保留现有锚点机制；颜色扩为黄/绿/蓝/红/紫 |
| 下划线 | 文字选区 → quads | 渲染层将 quads 转为每行底部的下划线段；复用同一锚定与重锚逻辑 |
| 文本框 | 页面绝对坐标矩形 + 富文本内容 | 不锚定文字（锚定空白区/图表旁），存页面坐标；支持拖动、缩放、编辑、删除；背景色可调 |
| 区域高亮 | 页面绝对坐标矩形 | 复用 `visual_region` 的框选交互（拖拽 → 调边角 → 确认）；半透明色块覆盖，可选附带框选区域的裁图提问 |
| AI 问答批注（新增） | 文字选区/区域 → 锚点 + 问答对 | 见下"AI 问答存为批注" |

**AI 问答存为批注（新增）**：Assistant 回答卡片新增"保存为批注"按钮，一键将当时的**提问 + 回答 + 原文选段/框选区域**固化为一条批注（沿用现有 `create_ai_saved` / saved-explanations 机制扩展，该机制已支持"AI 投影 + 来源校验"）。要点：
- 批注正文为 Markdown（问为引用块、答为正文），侧栏与悬浮卡片中渲染；
- 锚定：有选区时锚定选区（重锚机制同样适用）；仅框选区域提问时锚定区域矩形；
- 保存后进入批注列表，可随批注一起写回 PDF / 导出 Markdown（M4）；
- Master 回答不自动保存，仅 Assistant 单轮可保存，与"Assistant 临时、Master 长期"的分层一致。

**批注 CRUD（增改删）**：所有批注类型统一支持完整生命周期管理：
- **增**：四类交互入口（选区/框选/文本框放置），AI 问答批注经"保存为批注"创建；
- **改**：文字锚定类（高亮/下划线）可改颜色、改备注；文本框/区域高亮可拖动、缩放、编辑内容；AI 问答批注可编辑备注（问答回溯到原文可重新提问）；全部走 `PATCH` 接口；
- **删**：侧栏与悬浮卡片均可删除，二次确认；走 `DELETE` 接口；
- 批注列表按页码/时间/类型筛选，点击跳转到批注位置。

**批注写回 PDF 文件（用户确认：不设"原书不可变"限制，面向实际使用）**：所有批注（含 AI 问答批注）可写回 PDF 本体，技术可行——pypdf 支持生成 PDF 标准注释对象，五类批注与 PDF 原生注释类型一一对应：

| 批注类型 | PDF 注释类型 | 坐标映射 |
|----------|--------------|----------|
| 高亮 | Highlight（quadpoints） | 文字锚点 quads → PDF 用户空间坐标 |
| 下划线 | Underline（quadpoints） | 同上，按行取底线 |
| 文本框 | FreeText / Text（含 Popup 内容） | 归一化矩形 → 页面矩形；正文写入注释内容流 |
| 区域高亮 | Square | 归一化矩形 → 页面矩形，半透明填充色 |
| AI 问答批注 | Text 注释（图标锚点）+ Popup 展开问答 Markdown 纯文本 | 锚定选区/区域的代表点 |

**写回模式（设置页开关，默认"手动"）**：

| 模式 | 行为 | 适用 |
|------|------|------|
| 关闭 | 批注仅存本地数据库 | 不想动 PDF 文件 |
| 手动导出 | 点"导出批注版 PDF"生成 `{书名}_批注版.pdf` 副本 | 想保留原文件干净 |
| **自动写回** | 每次批注增/改/删后，自动将变更同步写入 PDF 文件本体（原子写入：临时文件 + 替换，失败自动回滚并提示） | **实际使用主推**：批注跟着文件走，换设备/借给别人都能看到 |

**自动写回的实现要点**：
1. **写回目标可配**：`写回源文件`（直接修改用户的原 PDF，如 E 盘 WPS 云盘里的文件——云盘同步/分享后批注随行）或 `写回库内副本`（只改导入副本，原文件不动）；默认源文件，写入前对目标文件做一次 `.bak` 备份（数据目录内，保留最近 3 份）。
2. **Reader 标记**：写回的注释统一打标识（注释内容前缀 + 自定义 /NM 字段），用于区分"Reader 写的"和"其他工具写的"注释，实现增量同步。
3. **同步逻辑**：DB 为主存储，写回是单向同步（DB → PDF）。增/改对应更新或新建注释对象；删则按标记找到对应注释对象移除。云盘同步冲突、外部同时编辑等异常场景：写入失败时保持 DB 数据完整，状态栏提示重试。
4. **导入侧**：打开含 Reader 标记注释的 PDF 时自动关联为已有批注（换电脑迁移场景）；非 Reader 的外部注释只读展示，不并入。
5. 写回时做颜色映射（批注色 → PDF 注释 C 值）、竖排/旋转页坐标换算；无法映射的批注（重锚失败的）跳过并在状态栏列出。
6. 性能：大文件（如《针灸大成》数百 MB）写回采用增量策略——pypdf 整体重写耗时较长，自动模式下合并 5 秒内的连续操作做防抖批量写入，并在后台线程执行不阻塞阅读。

**技术要点**：
- **已确认决策：竖排页（如《针灸大成》《类经》古籍页）仅开放区域高亮与文本框两类批注**（坐标锚定不受排版影响）；导入向导检测到竖排版式时自动置灰文字锚定类批注入口，允许用户手动恢复尝试。
- 前端：PDF.js 渲染层之上加一个批注 overlay 层（SVG 或绝对定位 div），按 `page_index` 懒加载当页批注；文本框/区域高亮用页面归一化坐标（0–1）存储，缩放无关。
- 后端：新增 `POST /api/revisions/{id}/pages/{n}/annotations`（按 kind 分派）、`PATCH`（移动/缩放/编辑）、`DELETE`；坐标校验限定页内。
- 锚定批注保留现有的"证据修复"思路：PDF 换 revision 后可按 quote+context 重锚（`outline/evidence.py` 与 `tools/repair_book_evidence.py` 的既有机制扩展到批注）。
- 导出：所有批注可随 M4 一起导出为 Markdown 附录（按页码列表）。

### M3 大纲/目录修复（对应 G3）

**现状**：`outline/` 模块围绕"OCR 出的目录文本 + 证据修复"构建，针对 408 教材准备过；对任意 PDF 不保证可用，且目录点击跳转依赖准备好的证据。

**方案**（三条通道，按优先级自动选择，UI 可切换）：

1. **PDF 书签通道（新增，首选）**：pypdf 读取 `reader.outline`，递归解析为树（标题 + 目标页码/坐标），写入 outline 表并标记来源 `PDF_BOOKMARK`。前端目录树点击 → 定位到对应页（含页内 Y 偏移，若书签带 destination 坐标）。中文书签需处理 GBK/UTF-16 编码兼容。
2. **OCR 目录通道（保留）**：现有 OCR 目录识别 + 页码匹配流程，作为无书签 PDF 的兜底；对类经/针灸大成这类多级卷篇结构，允许人工校正层级与页码后发布。
3. **手动通道**：用户在目录面板手动添加/修改条目（标题 + 页码），满足识别失败的极端情况。

**验收**：示例教材中《经络腧穴学》《中医内科学》若有书签则书签点击跳转 100% 命中正确页；《针灸大成》《类经》人工校正后的目录可保存并跳转。

### M4 问答导出 Markdown（对应 G4）

**方案**：

- 导出范围（UI 勾选）：
  1. 单次 Assistant 对话（选中某一轮"问 + 答"或整段会话）；
  2. Master 知识点对话（整个话题）；
  3. 已保存的解释（saved explanations）；
  4. 批注汇总（可选附录）。
- Markdown 结构约定：

```markdown
# 《针灸大成》 · 选段问答
- 来源：第 37 页 · 2026-10-28
> 原文选段：……（quote + 页码链接）

## 问
……

## 答
……

### 追问
……
```

- 实现：后端新增 `GET /api/export/markdown?scope=...`，服务端拼装后以 `Content-Disposition: attachment` 下载；同时支持"保存到数据目录 exports/ 下"。AI 回答本身已是 Markdown 渲染，导出时原样保留（代码块、公式文本）。
- 文件名规则：`{书名}_{页码}_{主题}_{日期}.md`。

### M5 AI 配置扩展（对应 G5）

**现状**：`agent_runtime/runtime.py` 中 `PROVIDER_NAMES = ("deepseek", "zhipu", "openrouter")` 硬编码，`PROVIDER_DEFAULTS` 内置 endpoint/model，凭据走 Windows 凭据管理器（`credentials.py`）。

**方案**：将 provider 机制重构为**注册表 + 通用 OpenAI 兼容客户端**。国内主流厂商目前均提供 OpenAI 兼容的 chat/completions 接口，因此只保留一个通用 HTTP 客户端，各厂商只差一份预设：

| Provider | Endpoint（兼容模式） | 默认模型（可改） | 备注 |
|----------|---------------------|------------------|------|
| DeepSeek | `api.deepseek.com/chat/completions` | deepseek-chat | 现有，保留 |
| 智谱 GLM | `open.bigmodel.cn/api/paas/v4/chat/completions` | glm-4-flash | 现有，保留 |
| 通义千问 | `dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | qwen-plus | 阿里云百炼 |
| Kimi | `api.moonshot.cn/v1/chat/completions` | moonshot-v1-8k | 月之暗面 |
| 豆包 | `ark.cn-beijing.volces.com/api/v3/chat/completions` | 按接入点 ID 填写 | 火山方舟，模型名=ep-xxx |
| MiniMax | `api.minimax.chat/v1/text/chatcompletion_v2` | abab6.5s-chat | |
| 讯飞星火 | `spark-api-open.xf-yun.com/v1/chat/completions` | generalv3.5 | OpenAI 兼容网关 |
| 腾讯混元 | `api.hunyuan.cloud.tencent.com/v1/chat/completions` | hunyuan-turbo | |
| 硅基流动 | `api.siliconflow.cn/v1/chat/completions` | deepseek-ai/DeepSeek-V3 | 聚合平台，可跑多家开源模型 |
| 自定义 | 用户填写完整 URL | 用户填写 | 兜底 |

**技术要点**：
- `deepseek.py`/专用客户端合并为 `openai_compat.py` 通用客户端（流式 SSE 解析沿用现有实现）；`PROVIDER_DEFAULTS` 改为数据文件（`providers.yaml`）驱动，新增厂商不改代码。
- 凭据存储沿用 Windows 凭据管理器，target 命名 `guided-reader-{provider}`；.env 覆盖机制保留。
- 配置 UI：设置页新增"模型服务商"下拉 + Key 输入 + 连接测试按钮；Assistant / Master / Review 三个角色可分别指定 provider+model，沿用现有"无静默回退"原则。
- 移除 OpenRouter 的强制代理/fail-closed 逻辑（面向国内直连场景），改为可选代理配置。

### M6 OCR 替换为 Umi-OCR（对应 G6）

**现状**：内置 rapidocr（onnxruntime）做页级文字识别，依赖较重（opencv/onnxruntime），且安装时下载慢。

**方案**：OCR 从"进程内库"改为"本机外部服务"，项目内只留一个**适配层**。

1. **部署模型**：用户安装 Umi-OCR（解压即用，免费离线），在全局设置开启 HTTP 服务（默认 `127.0.0.1:1224`）。Reader 启动时健康检查 `GET /`，不可达时状态栏提示"OCR 服务未启动，扫描件选字/搜索不可用"，但文字层 PDF 完全不受影响。
2. **适配层**：新建 `reader_service/ocr/umi_client.py`，替换现有 rapidocr adapter，接口对齐现有 `jobs/` 识别流水线：
   - 页级按需识别：页面渲染为 PNG（复用现有 pypdfium2 渲染）→ base64 → `POST /api/ocr` → 解析返回的文本块框坐标 → 写入现有 foundation 文字层（保持选字/搜索/锚点数据结构不变）。
   - 整本预识别：走 Umi-OCR 文档识别接口（上传 → 轮询任务 → 下载 jsonl/双层 PDF），导入向导中异步执行。
   - 参数映射：语言模型库（简体/繁体/竖排）、排版方案（`tbpu.parser`）、方向纠正、忽略区域，均在导入向导暴露。
3. **依赖清理**：`pyproject.toml` 移除 `rapidocr`、`onnxruntime`（opencv 若无其他引用一并移除）；安装体积显著下降。
4. **降级链**：文字层 PDF → 直接用原生文字；无文字层 → 请求 Umi-OCR；Umi-OCR 不可用 → 只读模式（原项目"AI 不可用仍可读"原则扩展为"OCR 不可用仍可读"）。

### M7 平台与集成（对应 G7）

1. **Windows 本地端（基线）**：维持"本机服务 + Chrome/Edge 打开"形态；补两个工程化改进：
   - 提供 `start.bat`（含 `--data-dir`）与可选的打包脚本（PyInstaller），消除"手动装 Python/Node"门槛；
   - 启动后自动拉起默认浏览器打开带 token 的 READY URL。
2. **learningtools 集成（已拿到源码，方案落地）**：
   - learningtools 实际结构（源码分析）：C# 启动器 `启动平台.exe`（`_build/launcher/Program.cs`，.NET）——检测 8777 端口 → 未运行则拉起 `python -m http.server 8777 --bind 127.0.0.1` 托管站点（Python 定位顺序：pythonw.ini / PATH / WorkBuddy 托管路径）→ 打开默认浏览器；支持 `stop` 参数。前端为单文件 SPA（`index.html`，暗色主题，含 home + host 容器视图），现有工具：中医辞典（`tools/dict`，MDX 词库经 `_build/pipeline.py` 五段管线：导出→清洗→字段解码（康熙/说文/本草）→别名压缩→分片）、拼音标注（`tools/拼音标注.html` + pinyin-pro.js）。
   - **集成方案（服务互通，两个本地服务并存）**：Reader（8765）与 learningtools 平台（8777）各自独立服务，互不吞并：
     1. Reader 侧新增 **握手文件**：启动时把带 token 的 READY URL 写入 `<data-dir>/reader-launch.json`（仅本机可读），关闭/退出时删除；
     2. learningtools 侧：C# 启动器增加"启动 Reader"逻辑——检测 8765 是否存活（读握手文件验证），未运行则用现有 FindPython() 机制 spawn Reader 进程（`python -m reader_service --data-dir ...`），捕获 stdout 的 READY URL（或轮询握手文件）后打开；
     3. 平台首页新增"教材阅读"入口卡片，点击后复用上述逻辑打开 Reader；Reader 侧在书库页可选回跳 `http://127.0.0.1:8777/`（配置项，默认关闭）。
     4. 可选深化（远期）：Reader 的 Markdown 导出目录指向 learningtools 工具目录，辞典查询入口嵌入 Reader 侧栏（`tools/dict` 是纯静态分片 JSON，Reader 前端可直接加载查询）。
   - 备选路径 B（核心库抽取）：将 `foundation/annotation/outline/storage` 抽为 `reader-core` 包供 learningtools 直接依赖自建界面。工作量大，仅作远期选项。
3. 多用户/服务端方案继续不做（与原项目"本地优先"事实一致）。

### M9 AI Skill 系统（对应 G9）

**现状**：`assistant/skill.py` 已有单技能加载机制（读取 `assistant/skills/assistant-explanation/SKILL.md` 的 frontmatter + 正文，注入系统提示），但技能硬编码、不可配置、无选择入口。`pyproject.toml` 的 package-data 已声明 `assistant/skills/*/SKILL.md`，说明目录式多技能有预留。

**默认技能**：`yixue-zonghe`（医学综合，来自 `SomnusWei/Dailyread/SKILL`）。该技能不是纯提示词，而是完整技能包：

| 组成 | 内容 | 对 Reader 的意义 |
|------|------|------------------|
| `SKILL.md` | frontmatter（name/description/触发词）+ 七大功能路由 + 检索策略 | 可直接注入系统提示 |
| `中医教材/`（711 部 txt）+ `西医教材/` | 教材基线知识库，GB18030/UTF-8 混合编码 | 本地文件检索，需脚本支撑 |
| `倪海厦体系/` + `石学敏体系/` + `蒸馏产出/` | 专家特化库（默认融入回答） | 同上 |
| `scripts/search_textbooks.py` 等检索脚本 | 跨库关键词检索、素材提取、OCR 错字核验 | 需要 Reader 侧的工具调用层才能真正生效 |
| `references/` + `templates/` | 体例规范、HTML 模板 | 讲义类功能依赖，Reader 场景可裁剪 |

**提问流程（按用户要求）**：

```
用户发起提问 → ① Skill 选择（弹层列出可用技能，默认高亮 yixue-zonghe，
               可记住本书/本次会话的选择）
            → ② 读取所选 skill 的 SKILL.md 正文注入 system prompt
            → ③ 结合 PDF 选中文字/页面上下文
            → ④ 经配置的 AI API Key（provider + model）发起请求
```

**方案（两阶段）**：

1. **阶段 1：技能注册表 + 提示注入（P3 交付）**
   - 技能目录：`<data-dir>/skills/<skill-name>/SKILL.md`；启动时扫描所有含合法 frontmatter 的 SKILL.md 建立注册表（name、description、触发词）。
   - `skill.py` 从硬编码路径改为按注册表加载； Assistant 请求组装时注入所选技能正文 + 原有的"贴原文解释"约束。
   - 默认安装 `yixue-zonghe`：提供"从本地目录安装"（用户 clone/下载 Dailyread 后指向 `SKILL/` 目录，Reader 复制入 `<data-dir>/skills/yixue-zonghe/`）；阶段 1 只取 `SKILL.md` 与 `references/` 文本层。
   - 配置 UI：设置页新增"技能管理"——列表、启用/禁用、设为默认、从本地目录添加新技能、删除；后续可扩展从 GitHub URL 安装。
2. **阶段 2：技能脚本工具化（P4 后增强，可选）**
   - 在现有 `agent_runtime/runtime.py` 的会话循环中加入受控工具调用：把技能包 `scripts/` 下白名单脚本（如 `search_textbooks.py --keyword ...`）暴露为可调用工具，AI 回答前先检索本地知识库再作答——这是 yixue-zonghe"禁止凭记忆引原文"要求真正生效的前提。
   - 安全边界：仅允许执行技能目录内白名单脚本、参数透传前校验、禁止任意 shell；脚本输出截断后进入上下文。
   - 大上下文管理：yixue-zonghe 的 SKILL.md 正文较长，注入时压缩为"功能路由表 + 检索策略"摘要层，完整正文仅在命中对应功能时展开。

**与 M5 的关系**：skill 决定"用什么知识/风格回答"，provider（M5）决定"用哪家的模型回答"，两者正交——同一 skill 可跑在不同 API Key 上。

### M8 移除习题功能（对应 G8）

**删除清单**：
- 后端：`practice_catalog.py`、`practice_hint.py`、`practice_knowledge.py`、`practice_prototype.py`、`practice_review.py`、`practice_visual.py`；`server.py` 中全部 `/practice-*` 路由；启动时的 Practice 数据迁移步骤。
- 前端：`practice-fixture.js`、`practice-knowledge.js`、Practice 相关 UI/入口/样式。
- 数据库：`practice_*` 相关表通过一次性迁移 DROP（先备份）。
- 文档/README 同步清理习题描述。

**连带简化**：Learning Memory 中"收藏习题"类型移除，保留 Master 回答与已保存解释两类；Master Review 改为围绕选段/批注发起，不再依赖题目。

---

## 4. 数据模型变更摘要

| 表 | 变更 |
|----|------|
| `annotations` | +`kind`（HIGHLIGHT/UNDERLINE/TEXTBOX/REGION）、+`rect`（归一化坐标，TEXTBOX/REGION 用）、颜色枚举扩展 |
| `outlines` | +`source`（PDF_BOOKMARK/OCR/MANUAL）、+`dest_y`（页内偏移） |
| `books` / `revisions` | +`text_layer`（原生/OCR/无）、+`has_bookmarks` 检测标记 |
| `providers` | 新增：用户级 provider 预设与角色绑定（或仅存凭据管理器 + .env，避免新表，倾向后者） |
| `practice_*` | 全部 DROP |
| 其余（memory、master、assistant 会话） | 不变 |

---

## 5. 实施里程碑

| 阶段 | 内容 | 交付判据 |
|------|------|----------|
| P0 清理 | M8 习题移除 + 408 文案清理 + 依赖瘦身 | 项目可启动，无 Practice 残留路由/表 |
| P1 骨架通用化 | M1 导入向导 + M3 书签通道 + M6 Umi-OCR 适配层 | 五本示例教材可导入、可读、目录可跳转、扫描件可选字 |
| P2 批注 | M2 五类批注（含 AI 问答批注）+ 增改删 + 持久化 + 重锚 + PDF 写回三模式 | 批注创建/编辑/删除/跨会话保留；竖排页仅区域高亮/文本框；AI 问答可一键存为批注；自动写回模式下增改删实时同步进 PDF 文件 |
| P3 AI 与导出 | M5 多 provider + M4 Markdown 导出 + M9 Skill 阶段 1（注册表/选择器/默认 yixue-zonghe 提示注入） | 任选国内厂商配 Key 可问答；提问先选 skill；对话/批注可导出 .md |
| P4 集成与打包 | M7 启动脚本/打包 + learningtools 接入验证（用户提供仓库代码后细化）+ M9 阶段 2（技能脚本工具化，可选） | 双击可用；learningtools 能拉起并打开指定教材；skill 检索脚本可在白名单内被调用 |

依赖关系：P0 → P1 → P2/P3（可并行）→ P4。P1 中 M6（OCR）与 M1（导入）耦合最深，先行打通。

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Umi-OCR HTTP 并发能力弱（官方提示顺序调用） | 整本预识别慢、偶发连接拒绝 | 适配层做串行队列 + 失败重试；大书用文档识别接口离线批处理而非页级实时 |
| 竖排繁体古籍 OCR 质量不足（《针灸大成》《类经》） | 选字/搜索不准 | 验收标准分级：普通教材全文可用，古籍"区域提问+批注"为主，不承诺全文 OCR 质量；保留忽略区域与人工校正 |
| 竖排 PDF 的文字锚点/选区坐标错位 | 批注漂移 | 竖排书禁用文字锚定类批注，仅开放区域高亮/文本框（坐标锚定不受排版影响） |
| 各厂商接口细节差异（豆包 ep 接入点、MiniMax 非标准路径） | provider 兼容 | 注册表内允许每家覆盖少量参数模板；连接测试按钮先行验证 |
| learningtools 仓库不可见 | 集成方案无法细化 | 先按服务化边界（路径 A）留出 API 面，仓库可见后再对接 |
| 删除 Practice 的数据迁移 | 老数据丢失 | 迁移前自动备份 state.sqlite3（沿用现有"已验证备份"机制） |

---

## 7. 验收标准（以五本示例教材为准）

1. 五本 PDF（靳三针法、经络腧穴学、类经、针灸大成、中医内科学）全部导入成功并出现在书库。
2. 有书签的教材：目录点击 100% 跳转正确页；无书签教材：人工校正目录一次后可保存并跳转。
3. 扫描件页面：Umi-OCR 启动时，可选字、可搜索、可做各类批注；Umi-OCR 关闭时，仍可阅读与做区域高亮/文本框。
4. 批注跨重启保留，PDF 重新导入（同文件）后文字批注可重锚；选中 Assistant 回答可一键保存为批注；开启自动写回后，批注的增改删实时同步到 PDF 文件，用任意 PDF 阅读器打开该文件可见注释且位置正确；删除批注后 PDF 中对应注释同步消失。
5. 配置任一国内厂商 Key（建议默认 DeepSeek 或智谱，国内直连），选中原文提问正常返回；回答内术语可继续追问。
6. 提问时先弹出 Skill 选择（默认 `yixue-zonghe`）；换选其他技能后回答风格/知识来源随之变化；可通过配置界面从本地目录添加新技能。
7. 任一问答对话可导出为合法 Markdown 文件，包含来源页码与原文选段。
8. 项目内无 Practice 功能残留、无 rapidocr/onnxruntime 依赖。

---

## 附录 A：Umi-OCR 集成要点

- 发行版：GitHub Releases / 蓝奏云，`.7z` 解压即用，支持 PaddleOCR-json 与 RapidOCR-json 双引擎（建议 Paddle 引擎，速度快）。
- HTTP 服务默认 `127.0.0.1:1224`，需在全局设置允许（默认开启）。
- 图片识别：`POST /api/ocr`，body `{"base64": "...", "options": {...}}`，返回 `data` 为文本块数组（含框坐标与置信度）；`GET /api/ocr/get_options` 查询可用参数。
- 文档识别（整本 PDF）：上传文件 → 轮询任务状态 → 下载结果（支持双层可搜索 PDF / jsonl）。
- 关键 options：`ocr.language`（语言模型库，繁体需对应库）、`tbpu.parser`（排版方案）、`ocr.cls`（方向纠正）、`data.format`。
- 注意：并发弱，顺序调用；偶发 ECONNREFUSED 重试即可。

## 附录 B：现网代码落点速查（改造时直接定位）

- provider 硬编码：`src/reader_service/agent_runtime/runtime.py:28`（PROVIDER_NAMES）、`PROVIDER_DEFAULTS`、`credentials.py`（凭据 target）
- 批注：`src/reader_service/annotation/service.py`（`create_text`、`HIGHLIGHT_STYLES`）、`repository.py`
- 大纲：`src/reader_service/outline/`（service/repair/evidence/repository）
- 区域框选交互：`src/reader_service/visual_region.py` + 前端 `visual-selection.js`
- Practice：`src/reader_service/practice_*.py`（6 文件）+ `server.py` 路由 + 前端 `practice-*.js`
- OCR 现适配层：rapidocr adapter（见 `test_rapidocr_adapter.py`）与 `jobs/` 流水线
- 存储：`storage.py`（ManagedPaths：state.sqlite3 + blobs + tmp），数据目录由 `--data-dir` 指定
