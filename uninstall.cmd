@echo off
rem ==========================================================
rem  ASCII-only wrapper for uninstall.ps1
rem ==========================================================
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
if errorlevel 1 pause
endlocal
