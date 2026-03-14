param(
    [string]$ClashExe = "",
    [int]$WaitSeconds = 60,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptRoot "scheduled_task_runner.vbs"
$processNames = @("clash-verge", "verge-mihomo")

function Test-ClashRunning {
    return (@(Get-Process -Name $processNames -ErrorAction SilentlyContinue).Count -gt 0)
}

function Resolve-ClashExe {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        if (Test-Path $ExplicitPath) {
            return (Resolve-Path $ExplicitPath).Path
        }
        throw "Clash executable not found: $ExplicitPath"
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Clash Verge\clash-verge.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Clash Verge\clash-verge.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    $command = Get-Command "clash-verge.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Unable to locate clash-verge.exe. Pass -ClashExe explicitly."
}

$resolvedClashExe = Resolve-ClashExe -ExplicitPath $ClashExe

if (-not (Test-ClashRunning)) {
    Start-Process -FilePath $resolvedClashExe | Out-Null
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while (-not (Test-ClashRunning) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
}

if (-not (Test-ClashRunning)) {
    throw "Clash did not start within $WaitSeconds seconds."
}

$arguments = @("//nologo", $runner)
if ($Offline) {
    $arguments += "--offline"
}

Start-Process -FilePath "wscript.exe" -ArgumentList $arguments -WindowStyle Hidden | Out-Null
