@echo off
title Clip Downloader
cd /d "%~dp0"

echo.
echo  =============================================
echo   Clip Downloader
echo  =============================================
echo.

:: ── Check Python ──────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found.
    echo  Download from https://www.python.org
    echo  Make sure to tick "Add Python to PATH"
    pause & exit /b 1
)

:: ── Prefer ngrok (permanent URL) over cloudflared ─
set USE_NGROK=0
if exist ngrok.exe set USE_NGROK=1

:: ── Download cloudflared as fallback ──────────
if %USE_NGROK%==0 (
    if not exist cloudflared.exe (
        echo  Downloading Cloudflare Tunnel ^(one-time^)...
        powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
        echo  Done.
        echo.
    )
)

:: ── Start server in a new visible window ──────
echo  Starting server...
start "Clip Server" cmd /k "python server.py & pause"

:: ── Wait up to 180s for server ────────────────
echo  Waiting for server to be ready...
set TRIES=0
:WAIT
set /a TRIES+=1
if %TRIES% gtr 180 (
    echo.
    echo  ERROR: Server did not start. Check the Clip Server window.
    pause & exit /b 1
)
timeout /t 1 /nobreak >nul
powershell -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% neq 0 goto WAIT
echo  Server ready.
echo.

:: ── Start tunnel ──────────────────────────────
if %USE_NGROK%==1 (
    echo  =============================================
    echo   Starting ngrok...
    echo   https://collielike-semicivilized-josefina.ngrok-free.dev
    echo   Send that link to Kris and Wade to bookmark.
    echo  =============================================
    echo.
    ngrok.exe http --domain=collielike-semicivilized-josefina.ngrok-free.dev 8765
) else (
    echo  =============================================
    echo   Starting Cloudflare tunnel...
    echo   URL appears below - send to Kris and Wade.
    echo  =============================================
    echo.
    cloudflared.exe tunnel --url http://127.0.0.1:8765 --no-autoupdate
)

echo.
echo  Tunnel closed. You can close both windows.
taskkill /f /im python.exe >nul 2>&1
pause
