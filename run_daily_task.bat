@echo off
REM ============================================================
REM RNTS daily task - independent run, no web service needed.
REM Does: 1) fetch+store papers  2) generate monthly+yearly reports.
REM Logs:
REM   data\daily_task.log    (full verbose output)
REM   data\daily_status.log  (one clean line per run: time/status/counts)
REM Pure ASCII (no Chinese) to avoid codepage issues.
REM Pause: window stays open only when run interactively (double-click
REM   or from cmd). Headless scheduled tasks auto-skip the pause.
REM   Pass "nopause" argument to force no pause, e.g. for a scheduled
REM   task configured to show a window:  run_daily_task.bat nopause
REM NOTE: logging is configured inside app/daily_run.py (basicConfig),
REM   so per-source fetch results / retries / exceptions appear in log.
REM ============================================================
cd /d "%~dp0"

REM Python 3.13 writes UTF-8 to stdout while cmd defaults to codepage 936;
REM align both ends so the log files stay readable when typed to the console.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

set NO_PAUSE=0
if /i "%~1"=="nopause" set NO_PAUSE=1

echo ============================================================
echo  RNTS daily task
echo  Fetching papers and generating reports (takes 1-2 minutes)...
echo  DO NOT close this window until you see [OK] or [FAIL].
echo  Full output -> data\daily_task.log
echo  Daily status -> data\daily_status.log
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    goto :finish
)

if not exist "data" mkdir data

.venv\Scripts\python.exe -m app.daily_run > "data\_daily_run.tmp" 2>&1
set RC=%errorlevel%

echo.
echo ---- python output ----
if exist "data\_daily_run.tmp" (
    type "data\_daily_run.tmp"
    type "data\_daily_run.tmp" >> "data\daily_task.log"
    del /q "data\_daily_run.tmp" 2>nul
)
>>"data\daily_task.log" echo [RNTS] FINISHED rc=%RC%
echo [RNTS] FINISHED rc=%RC%

echo.
if %RC%==0 (
    echo [OK] Done. Database and reports updated.
) else (
    echo [FAIL] See output above or data\daily_task.log
)
echo ============================================================

:finish
REM Keep window open only when run interactively (a real console exists).
REM Detect console via CON; headless scheduled tasks skip the pause.
echo. > CON 2>nul && set INTERACTIVE=1
if defined INTERACTIVE (
    if %NO_PAUSE%==0 (
        echo Press any key to close this window...
        pause >nul
    )
)
