@echo off
rem ==========================================================
rem  ASCII-only wrapper.  The real work is in install.ps1
rem  (saved as UTF-8 WITH BOM so the Chinese text renders).
rem  Double-click this file to install.
rem ==========================================================
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 pause
endlocal
