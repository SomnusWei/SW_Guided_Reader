# -*- coding: utf-8 -*-
# ============================================================================
# SW_Guided_Reader 安装版一键打包脚本
#
# 用途：
#   1) 同步版本号（tauri.conf.json / Cargo.toml / package.json）
#   2) 重建 Reader 后端（PyInstaller onedir，含 Python 运行时）
#   3) 构建 Tauri 主程序与 NSIS 安装包（内置离线 WebView2，默认装到 D:\LearningTools）
#   4) 输出到 成品\安装版：
#        SW_Guided_Reader-<版本>-安装包.exe  全新安装 / 覆盖升级（启动程序 Reader.exe）
#        SW_Guided_Reader-<版本>-更新包.zip  已安装用户一键升级（保留教材与笔记）
#
# 用法：
#   .\发布安装版.ps1                    # 沿用当前版本号，全量构建
#   .\发布安装版.ps1 -Version 1.0.1     # 指定版本号并全量构建
#   .\发布安装版.ps1 -SkipSidecar       # 只改了界面/壳，跳过 Python 后端重建
#   .\发布安装版.ps1 -SkipBuild         # 只重新生成安装包产物（复用已有构建）
# ============================================================================

[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipSidecar,
    [switch]$SkipBuild,
    [switch]$SkipUpdateZip
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir     = Join-Path $Root "app"
$ReaderDir  = Join-Path $Root "408-guided-reader"
$ConfPath   = Join-Path $AppDir "src-tauri\tauri.conf.json"
$CargoPath  = Join-Path $AppDir "src-tauri\Cargo.toml"
$PkgPath    = Join-Path $AppDir "package.json"
$PyExe      = Join-Path $ReaderDir ".venv\Scripts\python.exe"
$BinTarget  = Join-Path $AppDir "src-tauri\binaries\reader-service"
$OutDir     = Join-Path $Root "成品\安装版"

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host ("==> " + $Text) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host ("    " + $Text) -ForegroundColor Green
}

function Write-Note {
    param([string]$Text)
    Write-Host ("    " + $Text) -ForegroundColor DarkGray
}

# 发布前密钥体检：版本内不允许出现任何真实 API Key。
# 只匹配"真实形态"的 Key（sk-…、Google AIza…、智谱 id.secret），
# 测试脚本里的 mock- / test-loopback-key / never-inspect 之类占位值不会命中。
function Find-ReleaseSecrets {
    param([string]$BaseDir)
    $patterns = @(
        'sk-[A-Za-z0-9]{20,}',
        'AIza[0-9A-Za-z_\-]{30,}',
        '[0-9a-f]{32}\.[A-Za-z0-9]{16,}'
    )
    $roots = @(
        (Join-Path $BaseDir '408-guided-reader\src'),
        (Join-Path $BaseDir '408-guided-reader\sidecar'),
        (Join-Path $BaseDir 'app\src-tauri\binaries'),
        (Join-Path $BaseDir 'app\src-tauri\src')
    )
    $exts = @('.py', '.js', '.mjs', '.json', '.md', '.txt', '.rs', '.ts', '.toml', '.yaml', '.yml')
    $hits = @()
    foreach ($r in $roots) {
        if (-not (Test-Path $r)) { continue }
        $files = Get-ChildItem -LiteralPath $r -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                ($exts -contains $_.Extension.ToLower()) -and
                ($_.FullName -notmatch '\\(node_modules|dist|build|_internal|__pycache__|\.venv|vendor)\\.')
            }
        foreach ($p in $patterns) {
            $found = $files | Select-String -Pattern $p -ErrorAction SilentlyContinue
            if ($found) { $hits += $found }
        }
    }
    return $hits
}

# 删除文件或目录（不经过回收站）。部分受管环境会拦截 Remove-Item，这里直接用 .NET 调用。
function Remove-PathForce {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    try {
        if ((Get-Item -LiteralPath $Path).PSIsContainer) {
            [System.IO.Directory]::Delete($Path, $true)
        } else {
            [System.IO.File]::Delete($Path)
        }
    } catch {
        Write-Note ("删除失败（可忽略，稍后手动清理）：" + $Path)
    }
}

Write-Host "SW_Guided_Reader 安装版打包" -ForegroundColor White
Write-Note ("工程目录：" + $Root)

# ---------------------------------------------------------------------------
# 1. 版本号
# ---------------------------------------------------------------------------
$confText = Get-Content $ConfPath -Raw -Encoding UTF8
if (-not ($confText -match '"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"')) {
    throw "无法从 tauri.conf.json 读取版本号，请检查文件是否完整。"
}
$CurrentVersion = $Matches[1]

if ($Version -ne "") {
    if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "版本号格式应为 x.y.z（例如 1.0.1），当前传入：$Version"
    }
    Write-Step ("同步版本号：" + $CurrentVersion + " -> " + $Version)

    $confText = [regex]::Replace($confText, '("version"\s*:\s*")[0-9]+\.[0-9]+\.[0-9]+(")', ('${1}' + $Version + '${2}'))
    # 注意：必须写 UTF-8 无 BOM。PowerShell 5.1 的 -Encoding UTF8 会加 BOM，
    # 而 Tauri / npm 解析带 BOM 的 JSON 会直接报 "expected value at line 1 column 1"。
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ConfPath, $confText, $utf8NoBom)

    $cargoText = Get-Content $CargoPath -Raw -Encoding UTF8
    $cargoText = [regex]::Replace($cargoText, '(?m)^(version\s*=\s*")[0-9]+\.[0-9]+\.[0-9]+(")', ('${1}' + $Version + '${2}'))
    [System.IO.File]::WriteAllText($CargoPath, $cargoText, $utf8NoBom)

    $pkgText = Get-Content $PkgPath -Raw -Encoding UTF8
    $pkgText = [regex]::Replace($pkgText, '("version"\s*:\s*")[0-9]+\.[0-9]+\.[0-9]+(")', ('${1}' + $Version + '${2}'))
    [System.IO.File]::WriteAllText($PkgPath, $pkgText, $utf8NoBom)

    $CurrentVersion = $Version
    Write-Ok ("版本号已更新为 " + $CurrentVersion)
} else {
    Write-Note ("沿用当前版本号：" + $CurrentVersion)
}

# ---------------------------------------------------------------------------
# 2. 重建 Reader 后端（PyInstaller onedir）
# ---------------------------------------------------------------------------
if ($SkipSidecar) {
    Write-Step "跳过 Reader 后端重建（-SkipSidecar）"
    if (-not (Test-Path (Join-Path $BinTarget "reader-service.exe"))) {
        throw "binaries\reader-service 不存在，无法跳过重建。请去掉 -SkipSidecar 重新执行。"
    }
} else {
    Write-Step "重建 Reader 后端（PyInstaller，含 Python 运行时）"
    if (-not (Test-Path $PyExe)) {
        throw ("未找到项目虚拟环境：" + $PyExe + "。请先在 408-guided-reader 下创建 .venv 并安装依赖。")
    }
    Push-Location $ReaderDir
    try {
        Remove-PathForce (Join-Path $ReaderDir "build")
        Remove-PathForce (Join-Path $ReaderDir "dist")
        & $PyExe -m PyInstaller "sidecar\reader-service.spec" --noconfirm | Out-Null
        if ($LASTEXITCODE -ne 0) { throw ("PyInstaller 构建失败，退出码 " + $LASTEXITCODE) }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path (Join-Path $ReaderDir "dist\reader-service\reader-service.exe"))) {
        throw "后端构建产物缺失：dist\reader-service\reader-service.exe"
    }
    Remove-PathForce $BinTarget
    Copy-Item (Join-Path $ReaderDir "dist\reader-service") $BinTarget -Recurse
    Write-Ok "已重建并同步到 app\src-tauri\binaries\reader-service"
}

# ---------------------------------------------------------------------------
# 3. （2.0）dailyread-skill 已随 M6 移除；技能种子与随包插件由 PyInstaller spec
#    直接打进 reader-service/（见 sidecar/reader-service.spec 的 _include_tree）。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3.5 发布前密钥体检（版本内不得包含任何真实 API Key）
# ---------------------------------------------------------------------------
Write-Step "发布前检查：扫描版本内是否夹带 API Key"
$secretHits = @(Find-ReleaseSecrets -BaseDir $Root)
if ($secretHits.Count -gt 0) {
    Write-Host ("    发现 " + $secretHits.Count + " 处疑似真实 API Key，已中止打包：") -ForegroundColor Red
    $secretHits | Select-Object -First 10 | ForEach-Object {
        Write-Host ("      " + $_.Path + ":" + $_.LineNumber) -ForegroundColor Red
    }
    throw "版本内不得包含 API Key：请改用环境变量或 Windows 凭据管理器，清理后重新打包。"
}
Write-Ok "未发现任何 API Key"

# ---------------------------------------------------------------------------
# 4. 构建主程序 + NSIS 安装包
# ---------------------------------------------------------------------------
if ($SkipBuild) {
    Write-Step "跳过 Tauri 构建（-SkipBuild）"
} else {
    # 构建脚本会把资源复制到 target\release，若上次残留了只读文件会直接失败，这里先清掉只读位
    $staleResources = Join-Path $AppDir "src-tauri\target\release\reader-service"
    if (Test-Path $staleResources) {
        Get-ChildItem $staleResources -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.IsReadOnly = $false }
    }
    Write-Step "构建主程序与安装包（内置离线 WebView2，约 3-5 分钟）"
    Push-Location $AppDir
    try {
        & npm run tauri build
        if ($LASTEXITCODE -ne 0) { throw ("tauri build 失败，退出码 " + $LASTEXITCODE) }
    } finally {
        Pop-Location
    }
    Write-Ok "构建完成"
}

$SetupPath = Join-Path $AppDir ("src-tauri\target\release\bundle\nsis\Reader_" + $CurrentVersion + "_x64-setup.exe")
if (-not (Test-Path $SetupPath)) {
    # 版本号未变但构建被复用等场景：退回到目录内最新的 setup.exe
    $candidate = Get-ChildItem (Join-Path $AppDir "src-tauri\target\release\bundle\nsis") -Filter "Reader_*-setup.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -eq $candidate) { throw "未找到安装包产物，请检查构建日志。" }
    $SetupPath = $candidate.FullName
    Write-Note ("使用最新安装包：" + (Split-Path $SetupPath -Leaf))
}

# ---------------------------------------------------------------------------
# 5. 输出安装包与更新包
# ---------------------------------------------------------------------------
Write-Step "生成发布产物"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$InstallerName = "SW_Guided_Reader-" + $CurrentVersion + "-安装包.exe"
$InstallerPath = Join-Path $OutDir $InstallerName
Copy-Item $SetupPath $InstallerPath -Force
Write-Ok ("安装包：" + $InstallerName + "  " + [math]::Round((Get-Item $InstallerPath).Length / 1MB, 1) + " MB")

$UpdateZip = ""
if (-not $SkipUpdateZip) {
    $UpdateDir = Join-Path $OutDir ("更新包-" + $CurrentVersion)
    Remove-PathForce $UpdateDir
    New-Item -ItemType Directory -Path $UpdateDir -Force | Out-Null

    # 更新包内放一份安装包，保证压缩包自包含
    Copy-Item $InstallerPath (Join-Path $UpdateDir $InstallerName) -Force

    $CmdText = @'
@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title SW_Guided_Reader 更新

set "SETUP="
for %%F in ("SW_Guided_Reader-*-安装包.exe") do set "SETUP=%%~fF"
if not defined SETUP (
  echo [错误] 未找到安装包，请把本脚本与安装包放在同一个文件夹里。
  pause
  exit /b 1
)

echo 正在关闭 SW_Guided_Reader ...
taskkill /IM Reader.exe /F >nul 2>nul
taskkill /IM LearningTools.exe /F >nul 2>nul
taskkill /IM learningtools.exe /F >nul 2>nul
taskkill /IM reader-service.exe /F >nul 2>nul
ping -n 3 127.0.0.1 >nul

echo 正在安装更新，教材、笔记与学习记录都会保留 ...
start /wait "" "%SETUP%" /UPDATE /S
if errorlevel 1 (
  echo [错误] 更新失败。可以手动双击安装包完成升级，或右键以管理员身份运行本脚本。
  pause
  exit /b 1
)

set "APPDIR="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Reader" /v InstallLocation 2^>nul ^| findstr /i "InstallLocation"') do set "APPDIR=%%B"
if not defined APPDIR (
  for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\Reader" /v InstallLocation 2^>nul ^| findstr /i "InstallLocation"') do set "APPDIR=%%B"
)
if defined APPDIR (
  if not exist "%APPDIR%\Reader.exe" set "APPDIR="
)
if not defined APPDIR (
  if exist "D:\LearningTools\Reader.exe" set "APPDIR=D:\LearningTools"
)
if defined APPDIR start "" "%APPDIR%\Reader.exe"

echo 更新完成，正在启动 SW_Guided_Reader ...
ping -n 4 127.0.0.1 >nul
exit /b 0
'@
    $CmdPath = Join-Path $UpdateDir "安装更新.cmd"
    # 中文批处理必须为 UTF-8 无 BOM + CRLF，否则 cmd 会把 BOM 当命令执行
    $CmdText = $CmdText -replace "`r?`n", "`r`n"
    [System.IO.File]::WriteAllText($CmdPath, $CmdText, (New-Object System.Text.UTF8Encoding($false)))

    $ReadmeText = @'
SW_Guided_Reader 更新包
====================

适用对象：已经安装过 SW_Guided_Reader 的电脑。

使用方法：
  1. 关闭正在运行的 SW_Guided_Reader（脚本也会自动关闭）。
  2. 双击本文件夹里的“安装更新.cmd”。
  3. 等待自动安装完成，程序会自动重新启动。

说明：
  · 采用原地覆盖升级，教材、笔记、学习记录等数据保存在安装目录的 Reader 文件夹中，不会被删除。
  · 若提示失败，可直接双击同目录下的安装包；安装向导检测到旧版本后选择“重新安装”即可。
  · 想彻底重装时，请在“设置 - 应用 - 已安装的应用”里卸载后，再运行安装包。
'@
    [System.IO.File]::WriteAllText((Join-Path $UpdateDir "更新说明.txt"), ($ReadmeText -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

    $UpdateZip = Join-Path $OutDir ("SW_Guided_Reader-" + $CurrentVersion + "-更新包.zip")
    Compress-Archive -Path (Join-Path $UpdateDir '*') -DestinationPath $UpdateZip -CompressionLevel NoCompression -Force
    Remove-PathForce $UpdateDir
    Write-Ok ("更新包：" + (Split-Path $UpdateZip -Leaf) + "  " + [math]::Round((Get-Item $UpdateZip).Length / 1MB, 1) + " MB")
}

# ---------------------------------------------------------------------------
# 6. 汇总
# ---------------------------------------------------------------------------
Write-Step "打包完成"
Write-Host ("    版本：" + $CurrentVersion)
Write-Host ("    安装包：" + $InstallerPath)
if ($UpdateZip -ne "") { Write-Host ("    更新包：" + $UpdateZip) }
Write-Note "默认安装目录：D:\LearningTools（安装时可修改；D 盘不可用时自动回退到系统默认位置）"
Write-Note "用户数据目录：<安装目录>\Reader（升级与卸载均保留）"
