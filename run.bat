@echo off
REM ============================================================
REM RNTS local launcher (foreground; close window to stop)
REM For manual / temporary runs
REM NOTE: keep this file pure ASCII (English only) so it runs
REM       under ANY Windows console code page without encoding
REM       issues. Chinese messages belong in the .md docs.
REM ============================================================
cd /d "%~dp0"

REM Match the console code page with Python's UTF-8 stdout, otherwise the
REM Chinese log lines (startup / scheduler) show up as mojibake on cp936.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found. Run setup_env.bat first - see LOCAL_DEPLOY.md.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
echo RNTS starting at http://localhost:8000  (press Ctrl+C to stop)

REM Auto-open the default browser ~3s later, giving the server time to boot.
REM The helper runs in a separate window so Ctrl+C here only stops uvicorn.
start "" /min cmd /c "timeout.exe /t 3 /nobreak >nul & start "" http://localhost:8000"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
