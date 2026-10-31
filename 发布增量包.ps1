# -*- coding: utf-8 -*-
# ============================================================================
# SW_Guided_Reader 增量包一键生成脚本
#
# 用途：
#   对比 git 基线，只把"改过的文件"打成一个小体积增量包，
#   已安装用户双击即可升级，无需重新下载数百 MB 的完整安装包。
#
# 产物（成品\增量包\）：
#   SW_Guided_Reader-<版本>-增量包-<时间戳>.zip
#     ├─ payload/            镜像安装目录结构，只含改动文件
#     ├─ manifest.json       版本、基线提交、文件清单（含 SHA256）
#     ├─ apply.ps1           执行体（自动定位安装目录、备份、覆盖、校验）
#     ├─ 安装增量更新.cmd     双击入口
#     └─ 增量说明.txt
#
# 用法：
#   .\发布增量包.ps1                     # 相对 HEAD 取改动（含未提交修改）
#   .\发布增量包.ps1 -BaseRef 6626311     # 相对指定提交取改动
#   .\发布增量包.ps1 -SkipSidecarRebuild  # 改了 .py 但不重建后端（不推荐）
# ============================================================================

[CmdletBinding()]
param(
    [string]$BaseRef = "HEAD",
    [string]$OutDir = "",
    [switch]$SkipSidecarRebuild
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Root      = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir    = Join-Path $Root "app"
$ReaderDir = Join-Path $Root "408-guided-reader"
$ConfPath  = Join-Path $AppDir "src-tauri\tauri.conf.json"
$PyExe     = Join-Path $ReaderDir ".venv\Scripts\python.exe"
if ($OutDir -eq "") { $OutDir = Join-Path $Root "成品\增量包" }

function Write-Step { param([string]$Text) Write-Host ""; Write-Host ("==> " + $Text) -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host ("    " + $Text) -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host ("    " + $Text) -ForegroundColor DarkGray }
function Write-Warn { param([string]$Text) Write-Host ("    " + $Text) -ForegroundColor Yellow }

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
        Write-Note ("删除失败（可忽略）：" + $Path)
    }
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}

Write-Host "SW_Guided_Reader 增量包生成" -ForegroundColor White
Write-Note ("工程目录：" + $Root)

# ---------------------------------------------------------------------------
# 1. 版本号与基线
# ---------------------------------------------------------------------------
$confText = Get-Content $ConfPath -Raw -Encoding UTF8
if (-not ($confText -match '"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"')) {
    throw "无法从 tauri.conf.json 读取版本号。"
}
$AppVersion = $Matches[1]

Push-Location $ReaderDir
try {
    # 用 cmd 包装 git：git 的 LF/CRLF 警告会走 stderr，直接调用会被 PowerShell 当成终止错误
    $baseCommit = (cmd /c ("git rev-parse --short " + $BaseRef + " 2>nul") | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $baseCommit -eq "") { throw ("无效的基线：" + $BaseRef) }
    # 改动文件 = 已跟踪修改 + 未跟踪新增
    $changed = @()
    $changed += (cmd /c ("git diff --name-only " + $BaseRef + " -- src/reader_service 2>nul"))
    $changed += (cmd /c "git ls-files --others --exclude-standard -- src/reader_service 2>nul")
    $changed = $changed | Where-Object { $_ -and $_.Trim() -ne "" } | ForEach-Object { $_.Trim() } | Sort-Object -Unique
} finally {
    Pop-Location
}

$changed = @($changed | Where-Object { $_ -notmatch '__pycache__|\.pyc$' })
if ($changed.Count -eq 0) {
    Write-Warn ("相对 " + $BaseRef + " 没有检测到 src\reader_service 下的改动，未生成增量包。")
    exit 0
}

Write-Step ("基线：" + $baseCommit + "，改动文件 " + $changed.Count + " 个")
$changed | ForEach-Object { Write-Note $_ }

$pyFiles    = @($changed | Where-Object { $_ -like "*.py" })
$dataFiles  = @($changed | Where-Object { $_ -notlike "*.py" })
$needsExe   = $pyFiles.Count -gt 0

# ---------------------------------------------------------------------------
# 2. 组装 payload（镜像安装目录结构）
# ---------------------------------------------------------------------------
$Stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$StageDir = Join-Path $OutDir ("_staging-" + $Stamp)
$PayloadDir = Join-Path $StageDir "payload"
New-Item -ItemType Directory -Path $PayloadDir -Force | Out-Null

$manifestFiles = @()
foreach ($rel in $dataFiles) {
    # src/reader_service/static/app.js  ->  reader-service/_internal/reader_service/static/app.js
    $inner = $rel -replace '^src[\\/]reader_service[\\/]', ''
    $inner = $inner -replace '\\', '/'
    $target = "reader-service/_internal/reader_service/" + $inner
    $src = Join-Path $ReaderDir ($rel -replace '/', '\')
    if (-not (Test-Path $src)) { Write-Warn ("源文件缺失，跳过：" + $rel); continue }
    $dest = Join-Path $PayloadDir ($target -replace '/', '\')
    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
    Copy-Item $src $dest -Force
    $manifestFiles += [pscustomobject]@{
        path = $target; bytes = (Get-Item $dest).Length; sha256 = (Get-Sha256 $dest)
    }
    Write-Ok ("数据文件：" + $target)
}

# .py 改动：源码被编译进 reader-service.exe，只能重建后替换可执行文件
if ($needsExe) {
    if ($SkipSidecarRebuild) {
        Write-Warn "检测到 .py 改动，但已指定 -SkipSidecarRebuild，后端不会更新（改动不会生效）。"
    } else {
        Write-Step "检测到 .py 改动，重建 Reader 后端（约 1-2 分钟）"
        if (-not (Test-Path $PyExe)) { throw ("未找到虚拟环境：" + $PyExe) }
        Push-Location $ReaderDir
        try {
            Remove-PathForce (Join-Path $ReaderDir "build")
            Remove-PathForce (Join-Path $ReaderDir "dist")
            & $PyExe -m PyInstaller "sidecar\reader-service.spec" --noconfirm | Out-Null
            if ($LASTEXITCODE -ne 0) { throw ("PyInstaller 构建失败，退出码 " + $LASTEXITCODE) }
        } finally {
            Pop-Location
        }
        $exeSrc = Join-Path $ReaderDir "dist\reader-service\reader-service.exe"
        if (-not (Test-Path $exeSrc)) { throw "后端构建产物缺失：dist\reader-service\reader-service.exe" }
        $dest = Join-Path $PayloadDir "reader-service\reader-service.exe"
        Copy-Item $exeSrc $dest -Force
        $manifestFiles += [pscustomobject]@{
            path = "reader-service/reader-service.exe"; bytes = (Get-Item $dest).Length; sha256 = (Get-Sha256 $dest)
        }
        Write-Ok "后端可执行文件已加入增量包（依赖无变化时才可只替换 exe）"
        Write-Note "若本次还升级了 requirements.txt 里的第三方库，请改用完整安装包。"
    }
}

if ($manifestFiles.Count -eq 0) { throw "没有可打包的文件，已中止。" }

# ---------------------------------------------------------------------------
# 3. manifest.json
# ---------------------------------------------------------------------------
$manifest = [pscustomobject]@{
    product      = "LearningTools"
    appVersion   = $AppVersion
    baseRef      = $BaseRef
    baseCommit   = $baseCommit
    builtAt      = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    fileCount    = $manifestFiles.Count
    totalBytes   = ($manifestFiles | Measure-Object -Property bytes -Sum).Sum
    files        = $manifestFiles
}
$manifestJson = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText((Join-Path $StageDir "manifest.json"), $manifestJson, (New-Object System.Text.UTF8Encoding($false)))

# ---------------------------------------------------------------------------
# 4. apply.ps1（执行体；文件本身必须是 UTF-8 带 BOM，否则 PowerShell 5.1 中文乱码）
# ---------------------------------------------------------------------------
$ApplyPs1 = @'
param([switch]$NoStart)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath = Join-Path $Root "manifest.json"
if (-not (Test-Path $manifestPath)) {
    Write-Host "增量包不完整：找不到 manifest.json。请重新解压后再试。" -ForegroundColor Red
    exit 1
}
$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Find-InstallDir {
    foreach ($hive in @("HKCU:", "HKLM:")) {
        $key = Join-Path $hive "Software\Microsoft\Windows\CurrentVersion\Uninstall\Reader"
        if (Test-Path $key) {
            $loc = (Get-ItemProperty $key -ErrorAction SilentlyContinue).InstallLocation
            if ($loc) {
                $loc = ([string]$loc).Trim().Trim('"')
                if (Test-Path (Join-Path $loc "Reader.exe")) { return $loc }
            }
        }
    }
    if (Test-Path "D:\LearningTools\Reader.exe") { return "D:\LearningTools" }
    return $null
}

$install = Find-InstallDir
if (-not $install) {
    Write-Host "没有自动找到 LearningTools 的安装目录。" -ForegroundColor Yellow
    $input = Read-Host "请输入安装目录（例如 D:\LearningTools，直接回车退出）"
    $install = ([string]$input).Trim().Trim('"')
    if ($install -eq "" -or -not (Test-Path (Join-Path $install "Reader.exe"))) {
        Write-Host "目录无效或该目录下没有 Reader.exe，已退出。" -ForegroundColor Red
        exit 1
    }
}
Write-Host ("安装目录：" + $install)

# 已安装版本对照（版本不一致时只提醒，不阻止）
try {
    $installedVer = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Reader" -ErrorAction SilentlyContinue).DisplayVersion
    if (-not $installedVer) {
        $installedVer = (Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Reader" -ErrorAction SilentlyContinue).DisplayVersion
    }
    if ($installedVer -and $installedVer -ne $manifest.appVersion) {
        Write-Host ("提示：本机已安装版本为 " + $installedVer + "，增量包对应 " + $manifest.appVersion + "。") -ForegroundColor Yellow
        Write-Host "      如果之前从未安装过该版本，请改用完整安装包。" -ForegroundColor Yellow
    }
} catch { }

Write-Host "正在关闭 LearningTools ..."
foreach ($name in @("Reader", "LearningTools", "learningtools", "reader-service")) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        try { $_.Kill() } catch { }
    }
}
Start-Sleep -Seconds 2

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = Join-Path $install ("Reader\_增量备份\" + $stamp)
$failed = @()
$updated = 0

foreach ($file in $manifest.files) {
    $relNative = $file.path -replace "/", "\"
    $target = Join-Path $install $relNative
    $source = Join-Path (Join-Path $Root "payload") $relNative
    $backup = Join-Path $backupRoot $relNative

    if (-not (Test-Path $source)) {
        Write-Host ("增量包缺少文件：" + $file.path) -ForegroundColor Red
        $failed += $file.path
        continue
    }
    # 先备份原文件，便于失败回滚
    if (Test-Path $target) {
        New-Item -ItemType Directory -Path (Split-Path $backup -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $backup -Force
    }
    $parent = Split-Path $target -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }

    try {
        Copy-Item -LiteralPath $source -Destination $target -Force
        $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $file.sha256) {
            throw "校验值不一致"
        }
        Write-Host ("已更新：" + $relNative) -ForegroundColor Green
        $updated++
    } catch {
        Write-Host ("更新失败，正在还原：" + $relNative) -ForegroundColor Red
        if (Test-Path $backup) {
            try { Copy-Item -LiteralPath $backup -Destination $target -Force } catch { }
        }
        $failed += $file.path
    }
}

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host ("更新未完成：成功 " + $updated + " 个，失败 " + $failed.Count + " 个。") -ForegroundColor Red
    Write-Host "失败文件：" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor Red }
    Write-Host ("原文件备份在：" + $backupRoot)
    Write-Host "请确认 LearningTools 已完全退出后重试；若仍失败，请改用完整安装包覆盖安装。"
    exit 1
}

Write-Host ("全部完成：共更新 " + $updated + " 个文件。") -ForegroundColor Green
if (Test-Path $backupRoot) { Write-Host ("原文件备份在：" + $backupRoot) -ForegroundColor DarkGray }

if (-not $NoStart) {
    $exe = Join-Path $install "Reader.exe"
    if (Test-Path $exe) {
        Write-Host "正在启动 LearningTools ..."
        Start-Process -FilePath $exe -WorkingDirectory $install
    }
}
exit 0
'@
[System.IO.File]::WriteAllText((Join-Path $StageDir "apply.ps1"), ($ApplyPs1 -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($true)))

# ---------------------------------------------------------------------------
# 5. 双击入口（UTF-8 无 BOM + CRLF）
# ---------------------------------------------------------------------------
$CmdText = @'
@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title LearningTools 增量更新

echo ============================================
echo   LearningTools 增量更新
echo   只替换本次改动的文件，教材与笔记都会保留
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply.ps1"
if errorlevel 1 (
  echo.
  echo [失败] 更新未完成，请查看上方中文提示。
  pause
  exit /b 1
)

echo.
echo 更新成功。
pause
exit /b 0
'@
$CmdPath = Join-Path $StageDir "安装增量更新.cmd"
[System.IO.File]::WriteAllText($CmdPath, ($CmdText -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

$notes = @'
LearningTools 增量包
====================

适用对象：已经安装过 LearningTools 同版本（见 manifest.json 的 appVersion）的电脑，
          且只是局部小改动（界面脚本、模板、静态资源等），无需重新下载完整安装包。

使用方法：
  1. 双击“安装增量更新.cmd”。
  2. 脚本会自动关闭正在运行的程序，定位安装目录（默认 D:\LearningTools），
     备份原文件到 <安装目录>\Reader\_增量备份\时间戳\，然后覆盖改动文件并逐个校验 SHA256。
  3. 完成后自动重新启动 LearningTools。

注意：
  · 只覆盖改动文件，教材、笔记、学习记录（Reader 目录）不受影响。
  · 若出现“更新失败”，原文件已自动还原；请确认程序完全退出后重试。
  · 若本机安装的不是同版本，或失败重试仍不成功，请改用完整安装包。
  · 若改动涉及第三方依赖（requirements.txt），必须用完整安装包。
'@
[System.IO.File]::WriteAllText((Join-Path $StageDir "增量说明.txt"), ($notes -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

# ---------------------------------------------------------------------------
# 6. 压缩输出
# ---------------------------------------------------------------------------
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$ZipPath = Join-Path $OutDir ("LearningTools-" + $AppVersion + "-增量包-" + $Stamp + ".zip")
Compress-Archive -Path (Join-Path $StageDir '*') -DestinationPath $ZipPath -CompressionLevel Optimal -Force
Remove-PathForce $StageDir

Write-Step "生成完成"
Write-Host ("    增量包：" + $ZipPath)
Write-Host ("    文件数：" + $manifestFiles.Count + "，压缩前 " + [math]::Round(($manifest.totalBytes / 1KB), 1) + " KB，压缩后 " + [math]::Round(((Get-Item $ZipPath).Length / 1KB), 1) + " KB")
Write-Note ("基线提交：" + $baseCommit)
