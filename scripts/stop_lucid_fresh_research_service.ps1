# Gracefully stop the LUCID fresh research→angles service.
param(
    [string]$DatasetRoot = "datasets/prep_runs/LUCID/tangents_db_v1_fresh",
    [int]$WaitSeconds = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not [System.IO.Path]::IsPathRooted($DatasetRoot)) {
    $DatasetRoot = Join-Path $RepoRoot $DatasetRoot
}
$ServiceDir = Join-Path $DatasetRoot "service"
$PidFile = Join-Path $ServiceDir "worker.pid"
$StopFile = Join-Path $ServiceDir "stop.requested"
$StateFile = Join-Path $ServiceDir "state.json"

New-Item -ItemType Directory -Force -Path $ServiceDir | Out-Null
New-Item -ItemType File -Force -Path $StopFile | Out-Null
Write-Output "wrote $StopFile"

if (-not (Test-Path $PidFile)) {
    Write-Output "no worker.pid; stop sentinel written"
    exit 0
}

$WorkerPid = [int](Get-Content $PidFile | Select-Object -First 1)
if (-not (Get-Process -Id $WorkerPid -ErrorAction SilentlyContinue)) {
    Write-Output "pid $WorkerPid not running"
    exit 0
}

$Deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $Deadline) {
    if (-not (Get-Process -Id $WorkerPid -ErrorAction SilentlyContinue)) {
        Write-Output "stopped pid=$WorkerPid"
        if (Test-Path $StateFile) { Get-Content $StateFile -Raw }
        exit 0
    }
    Start-Sleep -Seconds 2
}

Write-Warning "PID $WorkerPid still alive after ${WaitSeconds}s; use Stop-Process -Id $WorkerPid -Force if needed"
exit 2
