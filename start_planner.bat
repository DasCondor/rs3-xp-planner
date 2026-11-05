@echo off
rem Starts the RS3 XP Planner server (if it isn't already running) and opens the dashboard.
cd /d "%~dp0"
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8765/api/players | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "RS3 XP Planner server" /min python -m rsxp serve
  timeout /t 4 /nobreak >nul
)
start "" http://127.0.0.1:8765/
