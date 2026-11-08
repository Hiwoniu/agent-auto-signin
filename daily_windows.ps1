[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('install', 'status', 'uninstall')]
    [string]$Action,
    [ValidateRange(0, 23)][int]$Hour = 9,
    [ValidateRange(0, 59)][int]$Minute = 30,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$TaskName = 'AgentAutoSignin'
$CurrentTask = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue

if ($Action -eq 'status') {
    if (-not $CurrentTask) { throw "Scheduled task $TaskName is not installed." }
    $CurrentTask | Select-Object TaskName, State
    Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath '\' |
        Select-Object LastRunTime, LastTaskResult, NextRunTime
    exit 0
}
if ($Action -eq 'uninstall') {
    if ($CurrentTask) { Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false }
    exit 0
}

if (-not $Python) { $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
$Python = (Resolve-Path -LiteralPath $Python).Path
$Runner = Join-Path $PSScriptRoot 'scheduled_run.py'
$Pythonw = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (Test-Path -LiteralPath $Pythonw -PathType Leaf) { $Python = $Pythonw }

$Arguments = '"{0}" all --write-logs --hour {1} --minute {2}' -f $Runner, $Hour, $Minute
$TaskAction = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $PSScriptRoot
# The trigger only wakes the script every minute. Python applies the actual UTC+8 schedules.
$Trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
$Trigger.StartBoundary = [DateTimeOffset]::UtcNow.AddMinutes(1).ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $TaskAction -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName -TaskPath '\' | Select-Object TaskName, State
Write-Output ('Beijing time (UTC+8): daily {0:00}:{1:00}; Qoder 10:05.' -f $Hour, $Minute)
Write-Output "Logs: $env:LOCALAPPDATA\agent-auto-signin\logs"
