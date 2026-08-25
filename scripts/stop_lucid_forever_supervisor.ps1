param(
    [string]$DatasetRoot = "",
    [switch]$KeepStartupEntry
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
$Pids = @()
foreach ($Lane in @("data_load", "research", "angles")) {
    $StatePath = Join-Path $ServiceDir "campaign_${Lane}_state.json"
    if (-not (Test-Path -LiteralPath $StatePath)) {
        continue
    }
    try {
        $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        if ($null -ne $State.pid) {
            $Pids += [int]$State.pid
        }
        $State.status = "stopped"
        $State.updated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding utf8
    }
    catch {
        Write-Warning "Could not update $StatePath`: $($_.Exception.Message)"
    }
}

$SupervisorPidFile = Join-Path $ServiceDir "campaign_forever_supervisor.pid"
if (Test-Path -LiteralPath $SupervisorPidFile) {
    $Pids += [int](Get-Content -LiteralPath $SupervisorPidFile -Raw).Trim()
}
foreach ($ProcessId in ($Pids | Select-Object -Unique)) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

if (-not $KeepStartupEntry) {
    $StartupEntry = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Stego-LUCID-Forever.vbs"
    Remove-Item -LiteralPath $StartupEntry -Force -ErrorAction SilentlyContinue
}

Write-Output "Stopped LUCID forever supervisor and workers."
