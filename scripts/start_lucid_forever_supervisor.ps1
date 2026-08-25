param(
    [string]$DatasetRoot = "",
    [int]$PollSeconds = 30
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $DatasetRoot) {
    $DatasetRoot = Join-Path $RepoRoot "datasets\prep_runs\LUCID\tangents_db_v1_fresh"
}
$DatasetRoot = (Resolve-Path $DatasetRoot).Path
if (-not $DatasetRoot.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Dataset root must remain inside the stego-side-wing repository"
}

$ServiceDir = Join-Path $DatasetRoot "service"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Campaign = Join-Path $RepoRoot "scripts\run_lucid_generation_campaign.py"
$SupervisorPidFile = Join-Path $ServiceDir "campaign_forever_supervisor.pid"
$SupervisorStateFile = Join-Path $ServiceDir "campaign_forever_supervisor_state.json"

if (Test-Path -LiteralPath $SupervisorPidFile) {
    $ExistingPid = [int](Get-Content -LiteralPath $SupervisorPidFile -Raw).Trim()
    if ($ExistingPid -ne $PID -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
        throw "A LUCID forever supervisor is already running as PID $ExistingPid"
    }
}
Set-Content -LiteralPath $SupervisorPidFile -Value $PID -Encoding ascii

function Get-WorkerState([string]$Lane) {
    $StatePath = Join-Path $ServiceDir "campaign_${Lane}_state.json"
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-WorkerAlive([string]$Lane) {
    $State = Get-WorkerState $Lane
    if ($null -eq $State -or $null -eq $State.pid) {
        return $false
    }
    return $null -ne (Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue)
}

function Start-Worker([string]$Lane) {
    $BatchCount = if ($Lane -eq "data_load") { "5" } else { "1" }
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Stdout = Join-Path $ServiceDir "$Stamp.campaign_${Lane}_forever.stdout.log"
    $Stderr = Join-Path $ServiceDir "$Stamp.campaign_${Lane}_forever.stderr.log"
    $Arguments = @(
        $Campaign,
        "--dataset-root", $DatasetRoot,
        "--forever",
        "--batch-count", $BatchCount,
        "--data-load-batch-size", "5",
        "--llm-backend", "lm_studio",
        "--stage-mode", $Lane,
        "--failure-cooldown-seconds", "5",
        "--log-level", "INFO"
    )
    Start-Process -FilePath $Python `
        -ArgumentList $Arguments `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $Stdout `
        -RedirectStandardError $Stderr `
        -WindowStyle Hidden | Out-Null
}

$StartedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
while ($true) {
    $Workers = @()
    foreach ($Lane in @("data_load", "research", "angles")) {
        if (-not (Test-WorkerAlive $Lane)) {
            Start-Worker $Lane
            Start-Sleep -Seconds 2
        }
        $State = Get-WorkerState $Lane
        $Workers += [ordered]@{
            lane = $Lane
            pid = if ($null -ne $State) { $State.pid } else { $null }
            alive = Test-WorkerAlive $Lane
            status = if ($null -ne $State) { $State.status } else { "starting" }
            attempts = if ($null -ne $State) { $State.attempts } else { @{} }
            totals = if ($null -ne $State) { $State.totals } else { @{} }
        }
    }
    [ordered]@{
        status = "running"
        pid = $PID
        started_at_utc = $StartedAt
        updated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        poll_seconds = [Math]::Max(5, $PollSeconds)
        workers = $Workers
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $SupervisorStateFile -Encoding utf8
    Start-Sleep -Seconds ([Math]::Max(5, $PollSeconds))
}
