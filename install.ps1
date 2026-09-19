#Requires -Version 5.1
# ==========================================================
#  Codex 思考强度面板 —— 安装程序
#  双击 install.cmd 运行本脚本。
#  做了什么：查 Python -> 拷文件 -> 建桌面快捷方式。
#  不碰注册表，不碰系统目录，不碰 Codex 安装包。
# ==========================================================
param(
    # 安装目录。默认 %LOCALAPPDATA%\CodexEffortPanel。主要给自动化测试用。
    [string]$TargetDir,
    # 跳过创建桌面快捷方式（测试用）。
    [switch]$SkipShortcuts
)

$ErrorActionPreference = 'Stop'

$Src = $PSScriptRoot
if ($TargetDir) { $Dst = $TargetDir } else { $Dst = Join-Path $env:LOCALAPPDATA 'CodexEffortPanel' }

function Line { param($t, $c = 'Gray') Write-Host $t -ForegroundColor $c }
function Head {
    Write-Host ""
    Line "==========================================" Cyan
    Line "  Codex 思考强度面板 · 安装程序" Cyan
    Line "==========================================" Cyan
    Write-Host ""
}
function Ok   { param($t) Write-Host ("  [OK]   " + $t) -ForegroundColor Green }
function Warn { param($t) Write-Host ("  [!]    " + $t) -ForegroundColor Yellow }
function Bad  { param($t) Write-Host ("  [X]    " + $t) -ForegroundColor Red }

Head

# ---------------------------------------------------------- 1. Python
Line "-- 1/4  检查 Python --"
$pyExe = $null
foreach ($cand in @('python', 'py')) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $c) { continue }
    try {
        $null = & $c.Source -c "import tkinter" 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = $c.Source; break }
    } catch { }
}
if (-not $pyExe) {
    Bad "没找到带 tkinter 的 Python。"
    Write-Host ""
    Line "  这个面板需要 Python 3（自带 tkinter）。三种装法，任选一种：" Yellow
    Line "    A) 直接跑这一行（推荐，Windows 10/11 自带 winget）：" White
    Line "         winget install -e --id Python.Python.3.13" Gray
    Line "    B) 去 https://www.python.org/downloads/ 下载安装包，" White
    Line "       安装时务必勾选 Add python.exe to PATH。" Gray
    Line "    C) 如果已经装了但没进 PATH，重新运行安装包选 Repair。" Gray
    Write-Host ""
    $ans = Read-Host "  现在用 winget 自动装吗？(Y/N)"
    if ($ans -match '^[Yy]') {
        $wg = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $wg) { Bad "winget 也没有，请手动装（见上面 B）。"; Read-Host "按回车退出"; exit 1 }
        Line "  正在调用 winget ..." Gray
        winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
        Write-Host ""
        Warn "装完了。请关掉这个窗口，重新双击 install.cmd（PATH 需要新窗口才生效）。"
        Read-Host "按回车退出"
        exit 0
    }
    Read-Host "按回车退出"
    exit 1
}
$pyVer = (& $pyExe -c "import sys;print('.'.join(map(str,sys.version_info[:3])))") 2>$null
Ok ("Python " + $pyVer + "  ->  " + $pyExe)

# ---------------------------------------------------------- 2. 拷文件
Line ""
Line "-- 2/4  安装文件 --"
Line ("  目标目录：" + $Dst) Gray
if (-not (Test-Path $Dst)) { $null = New-Item -ItemType Directory -Path $Dst -Force }
$files = @('CodexEffortPanel.pyw', 'codex-max-effects-patch.py', 'launch-codex-default.cmd')
$copied = 0
foreach ($f in $files) {
    $s = Join-Path $Src $f
    if (-not (Test-Path $s)) { Warn ("源文件缺失，跳过：" + $f); continue }
    Copy-Item -LiteralPath $s -Destination (Join-Path $Dst $f) -Force
    $copied++
}
Ok ("已安装 " + $copied + "/" + $files.Count + " 个文件")

# 卸载脚本一并放过去，方便以后清干净
$un = Join-Path $Src 'uninstall.ps1'
if (Test-Path $un) { Copy-Item -LiteralPath $un -Destination (Join-Path $Dst 'uninstall.ps1') -Force }

# ---------------------------------------------------------- 3. 快捷方式
Line ""
Line "-- 3/4  创建桌面快捷方式 --"
if ($SkipShortcuts) {
    Warn "已跳过（-SkipShortcuts）"
} else {
$pyw = Join-Path (Split-Path $pyExe -Parent) 'pythonw.exe'
if (-not (Test-Path $pyw)) {
    # python.exe 可能是 py.exe 启动器；退回到同目录查找，再退回 python.exe
    $pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'
}
if (-not (Test-Path $pyw)) {
    Warn "没找到 pythonw.exe，快捷方式将用 python.exe（会闪一个黑框）。"
    $pyw = $pyExe
}
$target = Join-Path $Dst 'CodexEffortPanel.pyw'
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex 思考强度.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $pyw
$sc.Arguments        = '"' + $target + '"'
$sc.WorkingDirectory = $Dst
$sc.Description      = 'Codex 思考强度 —— 一键让滑块能拉到最高档并显示为 Ultra'
$sc.IconLocation     = "$env:SystemRoot\System32\shell32.dll,21"
$sc.Save()
Ok ("快捷方式：" + $lnk)

# 可选：把启动器也放一个到桌面（不打补丁也能启动 Codex）
$lnk2 = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex 启动(带补丁).lnk'
$sc2 = $ws.CreateShortcut($lnk2)
$sc2.TargetPath       = Join-Path $Dst 'launch-codex-default.cmd'
$sc2.WorkingDirectory = $Dst
$sc2.Description      = '启动 Codex，并在启动前自动打上思考强度补丁'
$sc2.IconLocation     = "$env:SystemRoot\System32\shell32.dll,137"
$sc2.Save()
Ok ("快捷方式：" + $lnk2)
}

# ---------------------------------------------------------- 4. 自检
Line ""
Line "-- 4/4  自检 --"
$ok = $true
foreach ($f in @('CodexEffortPanel.pyw', 'codex-max-effects-patch.py')) {
    $p = Join-Path $Dst $f
    if (Test-Path $p) { Ok ("存在 " + $f + "  (" + (Get-Item $p).Length + " 字节)") }
    else { Bad ("缺失 " + $f); $ok = $false }
}
$pkg = Get-AppxPackage -Name '*Codex*' | Sort-Object Version -Descending | Select-Object -First 1
if ($pkg) { Ok ("检测到 Codex：" + $pkg.Name + "  " + $pkg.Version) }
else { Warn "没检测到 Codex 包 —— 请先从 Microsoft Store 安装 Codex，再来点「一键生效」。" }

Write-Host ""
Line "==========================================" Cyan
if ($ok) {
    Line "  安装完成" Green
    Line ""
    Line "  下一步：双击桌面「Codex 思考强度」打开面板，" White
    Line "         点「一键生效」。" White
    Line ""
    Line "  注意：Codex 是单实例程序 —— 补丁要生效，" Yellow
    Line "       请先完全退出 Codex，再点「启动 Codex」。" Yellow
} else {
    Line "  安装未完成，请把上面的红色错误发出来" Red
}
Line "==========================================" Cyan
Write-Host ""
Read-Host "按回车退出"
