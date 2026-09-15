@echo off
REM ============================================================
REM RNTS one-time environment setup (Windows)
REM Creates .venv + installs dependencies + initializes DB.
REM Run ONCE. Then:
REM   - start web UI:  double-click run.bat -> http://localhost:8000
REM   - daily task:    powershell -ExecutionPolicy Bypass -File install_daily_task.ps1
REM NOTE: keep this file pure ASCII (English only) so it runs
REM       under ANY Windows console code page. Chinese messages
REM       belong in the .md docs.
REM ============================================================
cd /d "%~dp0"

REM Align console code page with Python's UTF-8 stdout so pip and the
REM setup messages are not garbled on the default cp936 console.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found on PATH. Install Python 3.12+ and tick "Add to PATH".
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [1/3] Creating virtual environment .venv ...
    python -m venv .venv
) else (
    echo [1/3] .venv already exists, skipping creation.
)

call .venv\Scripts\activate.bat

echo [2/3] Installing dependencies (may take a few minutes)...
pip install --upgrade pip
pip install -r requirements.txt

echo [3/3] Initializing database...
python -c "from app.database import init_db; init_db(); print('DB initialized OK')"

echo.
echo [OK] Environment ready.
echo   - Start web UI: double-click run.bat, then open http://localhost:8000
echo   - Daily task:   powershell -ExecutionPolicy Bypass -File install_daily_task.ps1
pause
