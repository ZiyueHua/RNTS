@echo off
REM ============================================================
REM RNTS report generator - generate reports from existing database.
REM Does NOT fetch papers.
REM Useful for regenerating reports after changing report format
REM or personal keywords/authors configuration.
REM Pure ASCII (no Chinese) to avoid codepage issues.
REM ============================================================
cd /d "%~dp0"

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

echo ============================================================
echo  RNTS report generator
echo  Generating reports from existing database...
echo  No paper fetching will be performed.
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Please run setup_env.bat first.
    goto :finish
)

.venv\Scripts\python.exe -c "from app.reports import generate_report_artifacts; print(generate_report_artifacts())"
set RC=%errorlevel%

echo.
if %RC%==0 (
    echo [OK] Reports generated successfully.
    echo Output: data\reports\
) else (
    echo [FAIL] Report generation failed.
)
echo ============================================================

:finish
echo.
pause