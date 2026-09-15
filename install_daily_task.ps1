# ============================================================
# RNTS 每日任务注册脚本（Windows 任务计划程序）
#
# 作用：注册一个系统任务 "RNTS_DailyFetch"，每天 08:00 自动运行
#       run_daily_task.bat（抓取 + 生成报告），无需常驻网页服务。
#
# 用法（在 RNTS 文件夹内以当前用户或管理员运行）：
#       powershell -ExecutionPolicy Bypass -File install_daily_task.ps1
#
# 管理：
#   立即测试  -> Start-ScheduledTask -TaskName "RNTS_DailyFetch"
#   卸载      -> Unregister-ScheduledTask -TaskName "RNTS_DailyFetch" -Confirm:$false
# ============================================================
$ErrorActionPreference = "Stop"

$project = $PSScriptRoot
$bat = Join-Path $project "run_daily_task.bat"

if (-not (Test-Path $bat)) {
    Write-Error "未找到 $bat，请确认本脚本位于 RNTS 文件夹内。"
    exit 1
}

# 用 cmd /c 运行 .bat 并加 nopause，确保后台运行时不会卡住等输入
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$bat`" nopause" `
    -WorkingDirectory $project

$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit 0

Register-ScheduledTask `
    -TaskName "RNTS_DailyFetch" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "RNTS 每日抓取与报告生成（独立任务，无需 Web 服务常驻）" `
    -Force

Write-Host "[OK] 已注册每日任务 'RNTS_DailyFetch'（每天 08:00 运行 run_daily_task.bat）。"
Write-Host "     立即测试一次： Start-ScheduledTask -TaskName 'RNTS_DailyFetch'"
Write-Host "     查看结果：     看 data\daily_status.log 末尾一行"
Write-Host "     卸载任务：      Unregister-ScheduledTask -TaskName 'RNTS_DailyFetch' -Confirm:`$false"
