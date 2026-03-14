param(
    [int]$Minutes = 10,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"

if ($Minutes -lt 5) {
    throw "Interval must be at least 5 minutes."
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptRoot "scheduled_task_runner.vbs"
$schtasks = Join-Path $env:SystemRoot "System32\schtasks.exe"

$taskPrefix = "ClashAutoMerge"
$logonTask = "$taskPrefix-AtLogon"
$repeatTask = "$taskPrefix-Every${Minutes}Min"
$taskAction = "wscript.exe $runner"
if ($Offline) {
    $taskAction += " --offline"
}

function Invoke-CmdLine {
    param([string]$CommandLine)
    cmd.exe /c $CommandLine
    if ($LASTEXITCODE -ne 0) {
        throw ("command failed. exit={0}; cmd={1}" -f $LASTEXITCODE, $CommandLine)
    }
}

function Remove-TaskIfExists {
    param([string]$TaskName)
    $commandLine = '"{0}" /Delete /TN "{1}" /F >nul 2>nul' -f $schtasks, $TaskName
    cmd.exe /c $commandLine
    if ($LASTEXITCODE -eq 0) {
        Write-Output ("Removed old task: {0}" -f $TaskName)
    }
}

Remove-TaskIfExists -TaskName $logonTask
Remove-TaskIfExists -TaskName $repeatTask

$createRepeat = '"{0}" /Create /F /TN "{1}" /SC MINUTE /MO {2} /TR "{3}"' -f $schtasks, $repeatTask, $Minutes, $taskAction
$runNow = '"{0}" /Run /TN "{1}"' -f $schtasks, $repeatTask

Invoke-CmdLine -CommandLine $createRepeat
Invoke-CmdLine -CommandLine $runNow

Write-Output ("Created task: {0}" -f $repeatTask)
Write-Output ("Interval: every {0} minutes" -f $Minutes)
Write-Output ("The task is also started once immediately after installation.")
Write-Output ("Runner: {0}" -f $runner)
