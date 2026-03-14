param(
    [string]$PythonExe = "",
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $scriptRoot "logs"
$logFile = Join-Path $logDir ("scheduled-task-{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$mutexName = "Global\ClashAutoMergeTaskLock"
$processNames = @("clash-verge", "verge-mihomo")

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Test-ClashRunning {
    return (@(Get-Process -Name $processNames -ErrorAction SilentlyContinue).Count -gt 0)
}

if (-not $PythonExe) {
    $candidate = Join-Path $env:USERPROFILE "anaconda3\python.exe"
    if (Test-Path $candidate) {
        $PythonExe = $candidate
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) {
            throw "Python executable not found."
        }
        $PythonExe = $cmd.Source
    }
}

$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$hasLock = $false

try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) {
        Write-Log "Previous scheduled run is still active. Skip this run."
        exit 0
    }

    if (-not (Test-ClashRunning)) {
        Write-Log "Clash is not running. Skip this run."
        exit 0
    }

    Write-Log ("Scheduled run started. Python={0}" -f $PythonExe)

    $arguments = @(
        "-X",
        "utf8",
        (Join-Path $scriptRoot "clash_auto_merge.py"),
        "--no-popup"
    )
    if ($Offline) {
        $arguments += "--offline"
    }

    $output = & $PythonExe @arguments 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Add-Content -Path $logFile -Value ($line.ToString()) -Encoding UTF8
    }
    Write-Log ("Scheduled run finished. exit={0}" -f $exitCode)
    exit $exitCode
}
catch {
    Write-Log ("Scheduled run failed: {0}" -f $_.Exception.Message)
    throw
}
finally {
    if ($hasLock) {
        $mutex.ReleaseMutex() | Out-Null
    }
    $mutex.Dispose()
}
