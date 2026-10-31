# Guided Reader + learningtools 平台 → Tauri 2.0 单一应用 可行性方案

> 日期：2026-10-29 · 现状基线：commit `92e3468`（pytest 673 passed / npm test 146 passed）
>
> **✅ 已确认实施（2026-10-29 用户拍板）**：采用路线 B（Tauri 壳 + Python sidecar）。
> 两条硬性要求：① **所有工具全部收进 Tauri 应用内（WebView2 窗口），任何场景不调用系统浏览器**；② 设置页新增**批注/问 AI 自定义快捷键**（录制键盘按键方式录入）。数据目录采用便携模式（exe 旁 `Reader/`）。

---

## 一、结论先行

**可行，且推荐做。** 但不是"用 Rust 重写"，而是 **Tauri 2.0 壳 + 现有 Python 后端打包为 sidecar** 的混合方案：

| 判断项 | 结论 |
|---|---|
| 技术可行性 | ✅ 高。Python 后端保持原样打包成单个 exe，由 Tauri 拉起管理，前端 WebView 直接加载现有页面 |
| 现有代码复用率 | ✅ 约 95%。`reader_service`（673 个测试覆盖）与 `static/` 前端（146 个测试覆盖）**一行不改**即可工作 |
| 工作量 | 中。核心改造约 **3~5 个工作日**（含验证），见第五节分解 |
| 主要风险 | PyInstaller 打包体积（约 80~150 MB）、杀软误报、隐藏依赖漏包——均有成熟对策 |
| 收益 | 双击单 exe 启动、免装 Python、平台与 Reader 合一、告别"端口被占/进程残留"这类本次刚踩过的故障 |

---

## 二、现状架构盘点（迁移的起点）

```
learningtools-main/
├── 启动平台.exe        ← C# 单文件启动器（dotnet publish），拉起 serve.py
├── serve.py            ← Python 静态服务 + /api/reader 拉起接口（8777）
├── index.html / tools/ ← 平台首页与工具页（纯静态）
├── 408-guided-reader/  ← Reader 仓库
│   ├── src/reader_service/   后端：标准库 http.server（ThreadingHTTPServer，8765）
│   │                         依赖仅 pypdf / pypdfium2 / numpy
│   ├── src/reader_service/static/  前端：原生 ES Module JS + CSS，无框架、无构建步骤
│   ├── tests/ (673) + tests-js/ (146) + tests-e2e/
│   └── .venv/          ← Python 3.12.10 虚拟环境
├── Reader/              ← 数据目录（state.sqlite3 + blobs + skills/config.json）
└── dailyread-skill/     ← 技能包源
```

当前痛点（Tauri 化能一并解决的）：

1. **启动链太长**：启动平台.exe → serve.py → venv Python → Reader 服务 → 浏览器，任何一环被回收/占用就出故障（本次"导入 PDF 无反应"即是端口双绑定所致）
2. **依赖环境脆弱**：venv 必须存在且指向正确解释器，换机要重建
3. **两个"应用"两个窗口体系**：平台是浏览器标签页，Reader 也是浏览器标签页，无应用感
4. **无更新机制**：换版本靠整目录拷贝

---

## 三、目标形态定义

**一个安装包 / 一个 exe**：

- 双击 `LearningTools.exe` → 打开桌面窗口（原生窗口，不是浏览器标签）
- 窗口内即是平台首页（工具网格）+ Reader 阅读界面，可在应用内切换
- 不要求用户装 Python / Node / 配 venv
- 数据目录可继续用现有 `Reader/`（便携模式）或迁移到 `%APPDATA%`（标准模式，二选一）
- 带系统托盘（可选）、单实例（重复双击激活已有窗口）

---

## 四、三条路线对比

| | 路线 A：Rust 全重写 | **路线 B：Tauri 壳 + Python sidecar（推荐）** | 路线 C：维持现状优化 |
|---|---|---|---|
| 思路 | 后端 Rust 重写，前端照搬 | Tauri 管 GUI 与生命周期，PyInstaller 打包的后端作为 sidecar 子进程 | 不引入 Tauri，改进启动脚本 |
| 后端代码 | 全部重写（673 测试作废重写） | **100% 保留** | 保留 |
| 工作量 | 2~4 个月起 | 3~5 天 | 1~2 天 |
| 风险 | 高（PDF 渲染、批注几何、OCR 管线全是硬骨头） | 低（后端黑盒不变） | 无，但收益有限 |
| 单文件体验 | ✅ 最佳 | ✅ 良好（两个 exe：主程序 + sidecar，可合并安装） | ❌ 仍是脚本链 |
| 结论 | ❌ 不值得 | ✅ **采用** | 作为 B 失败时的退路 |

> 不选 Electron 的理由：Electron 自带 Chromium（安装包 +120MB），而本项目目标用户在 Windows 上 **WebView2 已随 Edge 预装**（本机已验证有 Edge），Tauri 安装包可控制在 10MB 以内 + sidecar 体积。

---

## 五、推荐方案详细设计（路线 B）

### 5.1 目标架构

```
LearningTools.exe (Tauri 2.0, Rust + WebView2)
├── 主窗口：加载现有 index.html（平台首页，几乎原样复用）
│    └── Reader 工具页 → 不再走 8777/8765 端口跳转，改为应用内路由/新窗口
├── Rust 侧职责（新增代码，量很小）：
│    ├── 启动时拉起 sidecar：reader-service.exe（PyInstaller 冻结的 reader_service）
│    ├── 端口协商：Rust 绑定 :0 拿一个空闲端口 → 通过参数/环境变量传给 sidecar
│    ├── stdout 握手：sidecar 打印 "READY http://127.0.0.1:<port>/?token=xxx"（现有逻辑已输出此行）
│    │    → Rust 解析后导航 WebView；sidecar 退出则窗口提示/自动重启
│    ├── 单实例（tauri-plugin-single-instance）+ 托盘（可选）
│    └── 退出时顺序关闭 sidecar（解决 WAL/进程残留）
└── reader-service.exe（sidecar，PyInstaller --onedir）
     └── 现有 reader_service 完整打包，仅加 3 处小改动（见 5.3）
```

### 5.2 对现有代码的侵入面（刻意压到最小）

| 文件 | 改动 | 说明 |
|---|---|---|
| `reader_service/__main__.py` | ① `--port 0` 支持随机端口，READY 行回传实际端口；② 握手文件改为可选（有 Tauri 父进程时走 stdout 即可）；③ 复用之前加的依赖自检（PyInstaller 打包后天然满足） | 约 30 行 |
| `serve.py` / `启动平台.exe` | **废弃**（被 Tauri 取代），保留一段时间作退路 | 删 0 行代码 |
| `static/` 前端 | 不改。唯一可选优化：Reader 页检测到嵌入 WebView 时隐藏"复制 URL"提示 | 0~10 行 |
| 平台 index.html | 移除"启动 Reader"按钮的 HTTP 轮询逻辑，改为 Tauri invoke 调 Rust 命令（`tauri-plugin` 前端 API） | 约 50 行 |

### 5.3 关键技术点

1. **sidecar 打包（PyInstaller）**
   - `pyinstaller --onedir reader-service.spec`；`--onedir` 比 `--onefile` 启动快数秒，推荐
   - 需显式收集：`pypdfium2`（自带 dll）、`numpy` 的 pyi 钩子、`static/` 与 `skills/*/SKILL.md`（`--add-data`）
   - 产物约 80~150 MB（numpy 占大头）；如体积敏感，可用 `--exclude-module` 裁剪 numpy 的无用子模块
2. **端口与 token 握手**：沿用现有 `READY URL` stdout 协议，Rust 侧 `Command::output` 逐行监听——比现在的 `reader-launch.json` 文件握手更可靠（本次故障的文件残留问题根除）
3. **数据目录**：`--data-dir` 参数照旧传。便携模式传 `<exe目录>\Reader`（现结构不变）；标准模式传 `%APPDATA%\LearningTools`。**建议第一阶段便携模式**，零迁移成本
4. **Umi-OCR**：保持外部进程方案不变；Tauri 侧可加"检测 Umi-OCR 是否在运行"的状态提示（调 1224 端口）
5. **更新**：用 `tauri-plugin-updater`（配静态 JSON 清单即可，不强制签名服务器）；第一阶段可先不做
6. **测试资产保全**：pytest / npm test 全部照跑（它们不经过 Tauri）；新增 Rust 侧集成测试：拉起 sidecar → READY → 打开 `/api/books` → 关闭 → 无残留进程

### 5.4 需要新装的工具链

| 工具 | 用途 | 备注 |
|---|---|---|
| Rust (rustup, stable-msvc) | Tauri 编译 | 首次编译约 5~10 分钟 |
| Node（已有 v22） | Tauri CLI / 前端工具 | 现成 |
| PyInstaller | 打包 sidecar | 装进现有 .venv 即可 |
| VS Build Tools（C++ 桌面负荷） | Rust MSVC 链接器 | dotnet SDK 已装，但 Rust 需要 MSVC 而非 .NET，需确认/补装 |
| WebView2 Runtime | Win11 自带；Win10 一般也有 | 本机 Edge 存在即满足 |

---

## 六、实施步骤与估时

| 阶段 | 内容 | 估时 | 验收标准 |
|---|---|---|---|
| P0 预研 | 装 Rust/MSVC；`create-tauri-app` 起骨架；空窗口跑通 | 0.5 天 | `npm run tauri dev` 出窗口 |
| P1 sidecar | 写 PyInstaller spec；冻结后端；手动拉起验证 673 测试对应的运行时行为（导入/渲染/批注抽查） | 1 天 | reader-service.exe 独立可跑，功能抽查通过 |
| P2 握手 | `--port 0` + stdout READY；Rust 侧拉起/监听/导航/退出清理 | 1 天 | 双击 exe → 窗口出书架；任务管理器无孤儿进程 |
| P3 平台整合 | index.html 移植进 Tauri；"打开 Reader"改为窗口内导航；单实例插件 | 1 天 | 平台与 Reader 同窗切换正常 |
| P4 打包分发 | NSIS 安装包 / 便携 zip；图标；冷启动计时 | 0.5 天 | 干净虚拟机（无 Python）安装后全功能可用 |
| P5 收尾 | e2e 补充、交接文档更新、旧 serve.py/启动平台 标记废弃 | 0.5 天 | 全套测试绿 |

**合计：约 4.5 天净工作量**（首次接触 Rust 会再加 0.5~1 天学习缓冲）。

---

## 七、风险与对策

| 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|
| PyInstaller 漏收隐藏依赖（如 numpy 子模块、pypdfium2 dll） | 高 | sidecar 启动即崩 | `--collect-all` 兜底；P1 阶段先用现有 673 测试在冻结产物上跑冒烟 |
| 杀软误报 PyInstaller 产物 | 中 | 用户机被隔离 | `--onedir` + 代码签名证书（后期）；误报时加白名单指引 |
| 体积超预期（>200MB） | 中 | 分发不便 | 裁剪 numpy/测试依赖；必要时 `--onefile` + UPX 压缩（牺牲启动速度） |
| WebView2 缺失（老 Win10） | 低 | 窗口打不开 | Tauri 安装器支持自动引导安装 WebView2 Runtime |
| Rust 学习曲线 | 确定 | 进度 +0.5~1 天 | 本项目 Rust 面极窄（进程管理 + HTTP 监听），模板代码为主 |
| 双端状态不同步（浏览器旧入口与 exe 同时开） | 中 | 又见端口冲突 | sidecar 改为**拒绝二次启动**（端口被占即退出并提示），从协议上根除本次故障模式 |

---

## 八、迁移后不参与打包、仅保留在仓库的东西

- `tests/`、`tests-js/`、`tests-e2e/`（开发期质量保障，不进安装包）
- `.venv/`、`node_modules/`（开发环境）
- `dailyread-skill/`（随 `--add-data` 打进 sidecar，源码仍留仓库）
- `serve.py`、`_build/launcher/`、`启动平台.exe`（P3 验收后标记废弃，留一个版本作回退）

---

## 九、验证清单（P4/P5 验收用）

- [ ] 干净 Windows（无 Python/Node）安装 → 双击 → 书架出现《中医内科学》
- [ ] 导入新 PDF → 列表即时刷新 → 阅读渲染正常（对照现有 DPI/旋转行为）
- [ ] 五类批注 + PDF 写回（写前备份仍生成）
- [ ] 设置页：技能勾选、AI Key 保存/清除（凭据管理器目标名不变）
- [ ] Umi-OCR 扫描件选字（服务开/关两种状态都有正确提示）
- [ ] 强杀 sidecar 进程 → 窗口提示并可一键重启；退出应用 → 无残留进程、无 WAL 残留
- [ ] 重复双击 exe → 激活已有窗口
- [ ] pytest 673 / npm test 146 依旧全绿（后端与前端未被打包改动波及）

---

## 十、一句话总结

把"启动平台.exe + serve.py + venv"这条脆弱的三段启动链，替换成"Tauri 窗口 + PyInstaller sidecar"的单段进程管理；**Python 后端与静态前端原封不动**，673+146 个测试继续护航，用一周内的投入换来真正的单一桌面应用。

---

## 十一、实施记录（2026-10-29）

| 阶段 | 状态 | 结果 |
|---|---|---|
| P0 预研 | ✅ | Rust 1.99.1 (msvc) + VS2022 BuildTools 14.44 已就绪，Tauri 2 骨架通过 cargo check |
| P1 sidecar | ✅ | PyInstaller --onedir 冻结 reader_service（70MB）；冻结产物独立冒烟：READY 正常、会话建立、**PDF 导入 HTTP 201** |
| P2 握手 | ✅ | Rust 分配空闲端口 → sidecar stdout READY 行解析（90s 超时）→ 应用内 WebviewWindow 打开（不调系统浏览器）；退出时 kill sidecar |
| P3 平台整合 | ✅ | 平台 index.html 作为主窗口；tools/reader.html 支持 Tauri 模式（invoke open_reader / reader_status），保留网页回退；单实例插件已接 |
| P4 打包 | ✅ | tauri build（NSIS + release exe）；便携包组装：exe + reader-service/ + dailyread-skill/ + Reader/ 数据 |
| 附加功能 | ✅ | 设置页快捷键录制（6 个动作），shortcuts.json 持久化，阅读页运行时分发；后端测试 +2 |
