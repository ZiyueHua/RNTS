# ============================================================
# RNTS 每日任务脚本（独立运行，无需 Web 服务已在跑）
#
# 功能（直接调用 app.scheduler.run_daily_fetch）：
#   1. 抓取各 RSS 源的文献更新数据并过滤
#   2. 写入 SQLite 数据库（link 唯一约束自动去重）
#   3. 抓取成功后自动生成「近一月更新报告」与
#      「近一年作者精选报告」，落盘到 data/reports/（.md + .html）
#
# 与 Web 服务（uvicorn）完全解耦：无论服务是否启动、无论当天是否已抓过，
# 本脚本都会无条件重新抓取并重新生成报告。报告写入 data/reports/ 后，
# 由运行中的 Web 服务通过 /reports 提供访问，并由坚果云同步目录同步到手机端。
#
# 注册为 Windows 计划任务（每日定时）示例：
#   操作   -> 启动程序
#   程序   -> powershell.exe
#   参数   -> -ExecutionPolicy Bypass -NoProfile -File "C:\路径\RNTS\run_daily_task.ps1"
#   起始于 -> C:\路径\RNTS
#
# 手动测试：直接双击本脚本，或在该目录执行
#   powershell -ExecutionPolicy Bypass -File .\run_daily_task.ps1
# 运行日志见 data\daily_task.log
# ============================================================

$ErrorActionPreference = "Stop"

$project  = $PSScriptRoot
$python   = Join-Path $project ".venv\Scripts\python.exe"
$logFile = Join-Path $project "data\daily_task.log"

if (-not (Test-Path $python)) {
    Write-Error ("未找到 " + $python + "，请先运行 setup_env.bat（Windows）或 bash setup_env.sh（Linux/macOS）建好环境。")
    exit 1
}

$logDir = Split-Path $logFile -Parent
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

# 切换到项目根目录，保证 import app 可用
Set-Location -Path $project

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value ("==== " + $ts + " [RNTS 每日任务] 开始 ====")

try {
    # init_db(): 幂等建表（服务未运行时也能入库）
    # run_daily_fetch(): 抓取 + 过滤 + 入库 + 生成两份报告
    # logging is configured inside app/daily_run.py (basicConfig)
    $output = & $python -m app.daily_run 2>&1
    $output | ForEach-Object { Add-Content -Path $logFile -Value $_ }

    $rc = $LASTEXITCODE
    Add-Content -Path $logFile -Value ($ts + " [RNTS 每日任务] 结束 exit=" + $rc)
    exit $rc
}
catch {
    Add-Content -Path $logFile -Value ($ts + " [RNTS 每日任务] 异常: " + $_)
    exit 1
}
