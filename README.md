# SW_Guided_Reader

SW_Guided_Reader 是一款面向个人学习的 Windows 桌面 PDF 引导阅读器：在阅读教材的同时完成批注、提问、笔记沉淀，并能把全部学习痕迹写回 PDF 原书。项目由 **Tauri 2 桌面外壳（Reader.exe）** 与 **Python 阅读后端（reader-service）** 组成，安装包完全自包含，目标电脑无需预装任何运行库。

## 功能总览

- **PDF 引导阅读**：整书 / 单页双视图，文字层选句引导、页面内搜索、书签大纲、阅读进度自动记录；扫描件经 OCR 后同样支持选字。
- **批注体系**：高亮 / 下划线 / 文本框 / 区域高亮 / 便签五种批注，预设色 + 自定义取色盘，批注面板支持筛选、置顶与跳转。
- **AI 助手**：针对当前页 / 所选内容提问，多技能体系（内置助手 + 自定义技能），支持追问链；「所选内容存为笔记」会自动把选中的渲染内容反向序列化为 Markdown 保存；整条回答与摘录卡片均按 Markdown 渲染。
- **PDF 注释回写**：全部批注可写回 PDF 原书成为标准 PDF 注释（AI 笔记生成「AI」徽标便签）。回写为全量镜像语义——删除 / 修改批注后再次回写，PDF 中的注释同步删除 / 更新；其他工具写入的注释永不动。写回源文件前自动保留最近 3 份备份，也可选择只导出「库内批注副本」。
- **笔记与导出**：Markdown 笔记本（编辑 / 分栏 / 预览、自动保存、历史搜索）；笔记与 AI 回答可一键导出 `.md`，支持系统「另存为」对话框自选位置。
- **随包插件**：中医学词典（划词查询）与拼音标注插件，随安装包内置。
- **数据安全**：教材、批注、笔记、AI 对话全部保存在本机；AI 服务的 API Key 由使用者在设置页自行填写，只存于本机 Windows 凭据管理器或环境变量，安装包为空配置、不含任何密钥。

## 安装与升级

- **全新安装**：下载 `SW_Guided_Reader-<版本>-安装包.exe` 双击安装。安装包内置离线 WebView2 与 Python 运行时，无需任何预装环境；默认安装到 `D:\LearningTools`（沿用历史安装目录以保留既有用户数据，安装向导中可修改，D 盘不可用时自动回退）。
- **启动**：安装后通过开始菜单或安装目录下的 **Reader.exe** 启动。
- **升级**：下载 `SW_Guided_Reader-<版本>-更新包.zip`，解压后双击「安装更新.cmd」，自动完成关闭程序、覆盖升级与重新启动；数据目录 `<安装目录>\Reader` 在升级与卸载时均保留。
- **静默安装 / 升级**：安装包支持 `/S`（静默）与 `/UPDATE /S`（原地静默升级）。

## 系统架构

```
SW_Guided_Reader/
├─ app/                  # Tauri 2 桌面外壳（产物 Reader.exe）
│  └─ src-tauri/
│     ├─ src/            # 外壳：窗口、sidecar 拉起、单实例、数据目录策略
│     ├─ nsis/           # NSIS 安装包模板（默认安装目录等定制）
│     ├─ binaries/       # 构建时放入 reader-service 后端
│     └─ dist/           # 启动页（由 构建外壳.py 从根 index.html 生成）
├─ 408-guided-reader/    # 阅读后端 reader-service（Python）
│  ├─ src/reader_service/# HTTP 服务 + 静态前端（原生 JS）+ 各业务模块
│  ├─ sidecar/           # PyInstaller 打包入口与 spec
│  ├─ default-skills/    # 技能种子（首启补种到数据目录）
│  ├─ plugins/           # 随包插件（词典 / 拼音标注）
│  └─ tests/ tests-js/ tests-e2e/   # pytest / node --test / Playwright e2e
├─ _build/               # 资源构建脚本（技能种子、插件数据等一次性工具）
├─ tools/                # 插件源数据（词典原始数据等，不参与直接分发）
├─ 构建外壳.py            # 启动页母本 index.html → app/dist/index.html
├─ 发布安装版.ps1         # 一键打包：后端 PyInstaller + Tauri + NSIS + 更新包
├─ 发布增量包.ps1         # 基于 git 基线的增量更新包生成
└─ gh_release.py         # 用系统凭据创建 GitHub Release 并流式上传安装包
```

运行时的调用链：**Reader.exe（Tauri）→ 拉起 reader-service.exe（PyInstaller sidecar，随机端口）→ WebView2 加载服务端 Web UI**。后端把带 token 的访问地址写入数据目录 `reader-launch.json`，外壳读到 READY 后跳转；后端进程随主窗口退出。

## 从源码构建

依赖：Node.js 22+、Rust（stable，MSVC）、Python 3.12（建议用 `408-guided-reader/.venv` 虚拟环境并安装其 `pyproject.toml` 依赖）。

```powershell
# 1) 生成启动页（app/dist/index.html）
python 构建外壳.py

# 2) 一键打包（后端 PyInstaller → Tauri/NSIS → 成品\安装版\）
powershell -ExecutionPolicy Bypass -File .\发布安装版.ps1

# 产物：
#   成品\安装版\SW_Guided_Reader-<版本>-安装包.exe
#   成品\安装版\SW_Guided_Reader-<版本>-更新包.zip
```

发布到 GitHub Release（使用 Windows 凭据管理器中的 GitHub 凭据，token 不落盘）：

```powershell
408-guided-reader\.venv\Scripts\python.exe gh_release.py
```

## 开发与测试

```powershell
# 后端（在 408-guided-reader/ 内）
.venv\Scripts\python.exe -m pytest tests/ -q     # Python 单测
npm test                                          # 前端逻辑（node --test）
# e2e：tests-e2e/ 下为 Playwright 脚本，可用 sidecar 模式跑真机链路
.venv\Scripts\python.exe -m reader_service --port 8902 --data-dir <数据目录> --no-open --token dev

# 前端外壳（在 app/ 内）
npm run tauri dev                                 # 开发模式
npm run tauri build                               # 仅构建主程序
```

## 数据与隐私

| 内容 | 位置 |
| --- | --- |
| 教材、批注、笔记、AI 归档、进度 | `<安装目录>\Reader\`（升级与卸载保留） |
| 随包插件状态 | `<数据目录>\plugins-state.json` |
| AI 服务凭据 | Windows 凭据管理器 / 环境变量（可选 `.env`） |

本机数据不上传任何服务器；AI 请求直接发往使用者自己配置的模型服务。

## 版本

当前版本 **2.0.0**：Tauri 单一应用架构（外壳 + Python sidecar）、批注全类型写回 PDF、AI 助手与 Markdown 笔记、词典 / 拼音插件、完全自包含安装包。更早的独立平台版本已停止维护，本仓库自 2.0.0 起以全新历史维护。
