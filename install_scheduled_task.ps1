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
$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"

$taskPrefix = "ClashAutoMerge"
$logonTask = "$taskPrefix-AtLogon"
$repeatTask = "$taskPrefix-Every${Minutes}Min"
$startupLauncher = Join-Path $startupDir "$logonTask.vbs"
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

function Remove-StartupLauncher {
    if (Test-Path $startupLauncher) {
        Remove-Item -Path $startupLauncher -Force
        Write-Output ("Removed old startup launcher: {0}" -f $startupLauncher)
    }
}

function Write-StartupLauncher {
    New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
    $offlineArgs = ""
    if ($Offline) {
        $offlineArgs = " --offline"
    }
    $content = @(
        "Option Explicit",
        "Dim shell",
        'Set shell = CreateObject("WScript.Shell")',
        ('shell.Run "wscript.exe ""{0}""{1}", 0, False' -f $runner, $offlineArgs)
    )
    Set-Content -Path $startupLauncher -Value $content -Encoding ASCII
}

Remove-TaskIfExists -TaskName $logonTask
Remove-TaskIfExists -TaskName $repeatTask
Remove-StartupLauncher

$createLogon = '"{0}" /Create /F /TN "{1}" /SC ONLOGON /TR "{2}"' -f $schtasks, $logonTask, $taskAction
$createRepeat = '"{0}" /Create /F /TN "{1}" /SC MINUTE /MO {2} /TR "{3}"' -f $schtasks, $repeatTask, $Minutes, $taskAction
$runNow = '"{0}" /Run /TN "{1}"' -f $schtasks, $repeatTask

try {
    Invoke-CmdLine -CommandLine $createLogon
    Write-Output ("Created task: {0}" -f $logonTask)
} catch {
    Write-Output ("ONLOGON task creation failed, fallback to Startup launcher: {0}" -f $_.Exception.Message)
    Write-StartupLauncher
    Write-Output ("Created startup launcher: {0}" -f $startupLauncher)
}
Invoke-CmdLine -CommandLine $createRepeat
Invoke-CmdLine -CommandLine $runNow

Write-Output ("Created task: {0}" -f $repeatTask)
Write-Output ("Interval: every {0} minutes" -f $Minutes)
Write-Output ("The task runs once at each logon and is also started once immediately after installation.")
Write-Output ("Runner: {0}" -f $runner)
