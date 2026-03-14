param(
    [int]$Minutes = 10
)

$ErrorActionPreference = "Stop"

$schtasks = Join-Path $env:SystemRoot "System32\schtasks.exe"
$taskPrefix = "ClashAutoMerge"
$logonTask = "$taskPrefix-AtLogon"
$repeatTask = "$taskPrefix-Every${Minutes}Min"

foreach ($taskName in @($logonTask, $repeatTask)) {
    $commandLine = '"{0}" /Delete /TN "{1}" /F >nul 2>nul' -f $schtasks, $taskName
    cmd.exe /c $commandLine
    if ($LASTEXITCODE -eq 0) {
        Write-Output ("Removed task: {0}" -f $taskName)
    } else {
        Write-Output ("Task not found or delete failed: {0}" -f $taskName)
    }
}
