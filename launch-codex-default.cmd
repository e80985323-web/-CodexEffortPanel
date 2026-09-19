@echo off
setlocal EnableExtensions
rem ==========================================================
rem  Codex launcher -- external-API mode with the max-effects patch
rem
rem  What it does, in order:
rem    1. locate the installed Codex (no hardcoded version)
rem    2. run codex-max-effects-patch.py to patch a writable copy
rem    3. launch that copy (falls back to the installed app)
rem
rem  ASCII-only on purpose: a .cmd containing non-ASCII bytes can be
rem  mis-parsed by cmd.exe (measured: "was unexpected at this time").
rem ==========================================================

rem --- locate the installed Codex -------------------------------------------
rem  The AppX folder name carries the version:
rem      <publisher>.Codex_<version>_x64__<hash>
rem  and it changes on EVERY app update.  C:\Program Files\WindowsApps cannot
rem  be listed with dir, so ask the package manager instead.  A wildcard query
rem  is used so this works no matter what the publisher id is.
set "EXE="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$p=Get-AppxPackage -Name '*Codex*' | Sort-Object Version -Descending | Select-Object -First 1; if($p){$e=Join-Path $p.InstallLocation 'app\ChatGPT.exe'; if(Test-Path $e){$e}}"`) do set "EXE=%%P"

if not defined EXE (
  echo [x] Codex package not found.
  echo     Is Codex installed from the Microsoft Store for this user?
  pause
  exit /b 1
)
if not exist "%EXE%" (
  echo [x] Codex executable not found:
  echo     %EXE%
  pause
  exit /b 1
)

rem --- proxy: OPTIONAL ------------------------------------------------------
rem  If a file named proxy.txt sits next to this script, its first non-empty
rem  line is used as the proxy, e.g.  http://127.0.0.1:7897
rem  No proxy.txt  ->  no proxy is applied (plain launch).
set "PROXY="
if exist "%~dp0proxy.txt" (
  for /f "usebackq delims=" %%L in ("%~dp0proxy.txt") do (
    if not defined PROXY set "PROXY=%%L"
  )
)
set "TRIES=3"
set "TIMEOUT=8"

rem --- patch a writable copy and launch THAT --------------------------------
rem  The installed package under C:\Program Files\WindowsApps is owned by
rem  TrustedInstaller and is NOT writable, so the patch goes into the plain
rem  copy Profile Manager keeps at
rem      %LOCALAPPDATA%\Codex Profile Manager\WindowsAppsCache\<pkg>\app
rem  and THAT copy is what gets launched.  If the copy is missing (first run,
rem  or after a Codex update) it is rebuilt automatically from the install.
rem
rem  CODEX_SPARKLE_ENABLED=false is REQUIRED for that copy: without an MSIX
rem  package identity the in-app updater throws and aborts startup before any
rem  window appears.  It also stops the app from self-updating, which would
rem  otherwise replace the patched build and silently drop the effect.
rem  If anything fails we fall back to the installed app, unchanged.
set "LAUNCH_EXE=%EXE%"
set "LAUNCH_MODE=PLAIN"
set "APPDIR="
for %%D in ("%EXE%") do set "APPDIR=%%~dpD"
set "PSCRIPT=%~dp0codex-max-effects-patch.py"
set "POUT=%TEMP%\codex_prep_%RANDOM%.txt"
if not exist "%PSCRIPT%" goto :prepared
where python >NUL 2>NUL
if errorlevel 1 (
  echo [!] python not found on PATH - launching the installed app unchanged
  goto :prepared
)
echo.
echo preparing external-API max-effects patch ...
python "%PSCRIPT%" prepare --app "%APPDIR:~0,-1%" --out "%POUT%"
if not exist "%POUT%" goto :prepared
set /p LAUNCH_EXE=<"%POUT%"
for /f "usebackq skip=1 delims=" %%M in ("%POUT%") do set "LAUNCH_MODE=%%M"
del "%POUT%" >NUL 2>NUL
:prepared
if not exist "%LAUNCH_EXE%" (
  echo [!] patched copy unavailable - launching the installed app unchanged
  set "LAUNCH_EXE=%EXE%"
  set "LAUNCH_MODE=PLAIN"
)
if /i "%LAUNCH_MODE%"=="PATCHED" (
  set "CODEX_SPARKLE_ENABLED=false"
) else (
  set "CODEX_SPARKLE_ENABLED="
)

if not defined PROXY goto :launch

rem official ChatGPT channel: renderer uses --proxy-server, engine uses env vars
set "HTTP_PROXY=%PROXY%"
set "HTTPS_PROXY=%PROXY%"
set "ALL_PROXY=%PROXY%"
set "NO_PROXY=localhost,127.0.0.1,::1"

set "PFILE=%TEMP%\codex_probe_%RANDOM%.txt"
set /a n=0
:probe
set /a n+=1
echo.
echo [%n%/%TRIES%] probing proxy %PROXY% ...
set "C1="
set "C2="
curl.exe -s -o NUL -w "%%{http_code}" -x "%PROXY%" --max-time %TIMEOUT% https://chatgpt.com/backend-api/me > "%PFILE%" 2>NUL
set /p C1=<"%PFILE%"
curl.exe -s -o NUL -w "%%{http_code}" -x "%PROXY%" --max-time %TIMEOUT% https://ab.chatgpt.com/v1/initialize > "%PFILE%" 2>NUL
set /p C2=<"%PFILE%"

if "%C1%"=="" goto :retry
if "%C1%"=="000" goto :retry
if "%C2%"=="" goto :retry
if "%C2%"=="000" goto :retry

echo     proxy OK ^(chatgpt.com=%C1% ab.chatgpt.com=%C2%^) - launching Codex now
del "%PFILE%" >NUL 2>NUL
goto :launch

:retry
echo     proxy NOT working ^(chatgpt.com=%C1% ab.chatgpt.com=%C2%^) - node may be down
if %n% LSS %TRIES% (
  echo     retrying in 2s ...
  ping -n 3 127.0.0.1 >NUL
  goto :probe
)
echo.
echo [x] %TRIES% probes failed - switch node in your proxy client, then run this again.
echo     Press Ctrl+C within 10s to abort, otherwise it will launch anyway.
ping -n 11 127.0.0.1 >NUL
del "%PFILE%" >NUL 2>NUL

:launch
echo launching Codex ...
echo mode: %LAUNCH_MODE%
if defined PROXY (
  start "" "%LAUNCH_EXE%" --proxy-server=%PROXY%
) else (
  start "" "%LAUNCH_EXE%"
)
exit /b 0
