@echo off
setlocal
cd /d "%~dp0"
REM ============================================================
REM RNTS - Sort config.yaml (manual)
REM ============================================================
REM NOTES (do not break these rules):
REM   * Keep this file PURE ASCII + CRLF. cmd reads it with the
REM     local codepage (GBK on Chinese Windows); UTF-8 Chinese
REM     bytes can swallow line breaks and break the script.
REM   * chcp 65001 + PYTHONIOENCODING=utf-8 must stay together:
REM     Python 3.13 writes UTF-8 to stdout, while cmd defaults to
REM     codepage 936. Without matching both ends the Chinese tips
REM     printed by sort_config.py show up as mojibake.
REM   * "nopause" is consumed by this BAT, never forwarded to
REM     python (argparse would reject it as unknown argument).
REM ============================================================

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

set "PY=.venv\Scripts\python.exe"
set "NOPAUSE=0"
set "ARGS="

:parse
if "%~1"=="" goto :run
if /i "%~1"=="nopause" goto :is_nopause
set "ARGS=%ARGS% %~1"
goto :next

:is_nopause
set "NOPAUSE=1"

:next
shift
goto :parse

:run
echo ============================================================
echo   RNTS - Sort config.yaml (manual)
echo ============================================================
echo.
echo Sort keywords / highlight authors alphabetically.
echo Your comments in config.yaml are preserved.
echo A backup is made as config.yaml.bak before writing.
echo.

if not exist "%PY%" (
  echo [ERROR] Virtualenv not found: %PY%
  echo         Please create .venv and install requirements.txt first.
  goto :END
)

"%PY%" sort_config.py %ARGS%
if errorlevel 1 goto :FAIL

echo.
echo [OK] Done.
goto :END

:FAIL
echo.
echo [ERROR] Failed. See the message above.

:END
if "%NOPAUSE%"=="0" (
  echo.
  pause
)
endlocal
