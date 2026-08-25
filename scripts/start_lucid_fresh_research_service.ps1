# Start the LUCID fresh research-to-angles service (detached).
param(
    [string]$DatasetRoot = "datasets/prep_runs/LUCID/tangents_db_v1_fresh",
    [int]$BatchCount = 1,
    [int]$BatchSize = 5,
    [double]$SleepHours = 24,
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not [System.IO.Path]::IsPathRooted($DatasetRoot)) {
    $DatasetRoot = Join-Path $RepoRoot $DatasetRoot
}
$ServiceDir = Join-Path $DatasetRoot "service"
New-Item -ItemType Directory -Force -Path $ServiceDir | Out-Null

$PidFile = Join-Path $ServiceDir "worker.pid"
if (Test-Path $PidFile) {
    $Existing = [int](Get-Content $PidFile | Select-Object -First 1)
    if (Get-Process -Id $Existing -ErrorAction SilentlyContinue) {
        Write-Error "Service already running as PID $Existing. Stop it first."
        exit 1
    }
}

$StopFile = Join-Path $ServiceDir "stop.requested"
if (Test-Path $StopFile) { Remove-Item -Force $StopFile }

$Stdout = Join-Path $ServiceDir "service.stdout.log"
$Stderr = Join-Path $ServiceDir "service.stderr.log"
$ArgList = @(
    "run", "python", "scripts/run_lucid_fresh_research_service.py",
    "--dataset-root", $DatasetRoot,
    "--batch-count", "$BatchCount",
    "--batch-size", "$BatchSize",
    "--sleep-hours", "$SleepHours",
    "--log-level", $LogLevel
)
$Proc = Start-Process -FilePath "uv" -ArgumentList $ArgList `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -PassThru -WindowStyle Hidden

$Deadline = (Get-Date).AddSeconds(30)
$WorkerPid = $null
while ((Get-Date) -lt $Deadline) {
    if (Test-Path $PidFile) {
        $WorkerPid = [int](Get-Content $PidFile | Select-Object -First 1)
        if (Get-Process -Id $WorkerPid -ErrorAction SilentlyContinue) { break }
    }
    Start-Sleep -Milliseconds 500
}

if (-not $WorkerPid) {
    Write-Warning ("uv launcher pid={0}; worker.pid not ready yet - check {1}" -f $Proc.Id, $Stderr)
} else {
    Write-Output ("started worker_pid={0} (launcher_pid={1})" -f $WorkerPid, $Proc.Id)
}
Write-Output ("dataset_root={0}" -f $DatasetRoot)
Write-Output ("state={0}" -f (Join-Path $ServiceDir "state.json"))
Write-Output ("stdout={0}" -f $Stdout)
Write-Output ("stderr={0}" -f $Stderr)
Write-Output "stop: scripts/stop_lucid_fresh_research_service.ps1"
