#Requires -Version 5.1
# ==========================================================
#  Codex 思考强度面板 —— 卸载程序
#  删掉安装目录、两个桌面快捷方式，并把 Codex 的可写副本还原。
#  不会卸载 Python，不会碰 Codex 本体。
# ==========================================================
$ErrorActionPreference = 'Continue'

$Dst = Join-Path $env:LOCALAPPDATA 'CodexEffortPanel'
$desk = [Environment]::GetFolderPath('Desktop')

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Codex 思考强度面板 · 卸载" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  将删除：" -ForegroundColor Yellow
Write-Host ("    目录    " + $Dst)
Write-Host ("    快捷方式 " + (Join-Path $desk 'Codex 思考强度.lnk'))
Write-Host ("    快捷方式 " + (Join-Path $desk 'Codex 启动(带补丁).lnk'))
Write-Host ""
Write-Host "  另外会把 Codex 的可写副本还原成原始文件（紫光和 Ultra 会消失）。" -ForegroundColor Yellow
Write-Host ""

$ans = Read-Host "  确认卸载？(Y/N)"
if ($ans -notmatch '^[Yy]') { Write-Host "  已取消。"; Start-Sleep -Seconds 2; exit 0 }

# 1) 还原 Codex 的可写副本
$eng = Join-Path $Dst 'codex-max-effects-patch.py'
if (Test-Path $eng) {
    $py = $null
    foreach ($c in @('python', 'py')) {
        $g = Get-Command $c -ErrorAction SilentlyContinue
        if ($g) { $py = $g.Source; break }
    }
    if ($py) {
        Write-Host ""
        Write-Host "  正在还原 Codex 可写副本 ..." -ForegroundColor Gray
        & $py $eng restore
    } else {
        Write-Host "  [!] 没找到 python，跳过还原（可手动删 %LOCALAPPDATA%\Codex Profile Manager\WindowsAppsCache）" -ForegroundColor Yellow
    }
}

# 2) 删快捷方式
foreach ($n in @('Codex 思考强度.lnk', 'Codex 启动(带补丁).lnk')) {
    $p = Join-Path $desk $n
    if (Test-Path $p) { Remove-Item -LiteralPath $p -Force; Write-Host ("  已删除 " + $n) -ForegroundColor Green }
}

# 3) 删目录
if (Test-Path $Dst) {
    Remove-Item -LiteralPath $Dst -Recurse -Force
    Write-Host ("  已删除 " + $Dst) -ForegroundColor Green
}

Write-Host ""
Write-Host "  卸载完成。" -ForegroundColor Green
Write-Host ""
Read-Host "按回车退出"
