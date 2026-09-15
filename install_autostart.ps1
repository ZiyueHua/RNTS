# ============================================================
# RNTS 开机自启安装脚本（Windows 任务计划程序）
#
# 用途：把 RNTS 注册为「用户登录时自动在后台启动」的任务，
#       实现本地 24/7 运行（无需 Docker）。
# 用法（以管理员或当前用户 PowerShell 运行）：
#       powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# 卸载：
#       Unregister-ScheduledTask -TaskName "RNTS" -Confirm:$false
# ============================================================
$ErrorActionPreference = "Stop"

$project = $PSScriptRoot
$pythonw = Join-Path $project ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $pythonw)) {
    Write-Error "未找到 $pythonw，请先创建虚拟环境并安装依赖（见 LOCAL_DEPLOY.md）。"
    exit 1
}

# 用 pythonw（无控制台窗口）后台运行 uvicorn 单进程
$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1" `
    -WorkingDirectory $project

# 用户登录时触发（若机器自动登录/每天登录，则等同于常驻）
$trigger = New-ScheduledTaskTrigger -AtLogOn

# 允许在电池/不接电源时运行；崩溃后自动重启；不设执行时限
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit 0

Register-ScheduledTask `
    -TaskName "RNTS" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "RNTS 量子物理学术新闻追踪系统（本地后台服务）" `
    -Force

Write-Host "[OK] 已注册任务计划程序任务 'RNTS'。"
Write-Host "     下次用户登录时将自动在后台启动 RNTS。"
Write-Host "     手动启动任务： Start-ScheduledTask -TaskName 'RNTS'"
Write-Host "     访问地址：     http://localhost:8000"
