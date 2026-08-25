# One-shot health tick for lucid_fresh_6x_20260815. Prints JSON status; exits 0 always.
$ErrorActionPreference = 'Continue'
$Root = 'D:\Master\code\stego\stego-side-wing'
$Campaign = Join-Path $Root 'metrics\evaluation_campaigns\lucid_fresh_6x_20260815'
$Runs = Join-Path $Campaign 'runs'
$StatePath = Join-Path $Campaign 'babysit_state.json'

function Get-Workers {
  @(Get-CimInstance Win32_Process | Where-Object {
      $_.Name -in @('python.exe', 'uv.exe') -and
      $_.CommandLine -match 'run_lucid_evaluation_campaign|lucid_fresh_6x_20260815.+batch_|run_actual_workload_e2e.py.+lucid_fresh_6x'
    })
}

function Test-Lm {
  try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8081/v1/models' -TimeoutSec 5
    return $true
  } catch { return $false }
}

$manifestPath = Join-Path $Campaign 'manifest.json'
$batchPosts = 25
$repeats = 6
$totalPosts = 674
if (Test-Path $manifestPath) {
  try {
    $man = Get-Content $manifestPath -Raw | ConvertFrom-Json
    $batchPosts = [int]$man.batch_posts
    $repeats = [int]$man.repeats_per_post
    $totalPosts = @($man.artifacts).Count
  } catch {}
}
$totalBatches = [Math]::Ceiling($totalPosts / [double]$batchPosts)
$lastPosts = $totalPosts - ($batchPosts * ($totalBatches - 1))
$lastRequested = $lastPosts * $repeats

$batches = @()
$complete = 0
$ok = 0
$fail = 0
$incomplete = @()
Get-ChildItem $Runs -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '^batch_\d{4}$' } |
  ForEach-Object {
    $sum = Join-Path $_.FullName 'summary.json'
    $bal = Join-Path $_.FullName 'balanced'
    $s = @(Get-ChildItem (Join-Path $bal 'output-results') -Filter *.json -EA SilentlyContinue).Count
    $f = @(Get-ChildItem (Join-Path $bal 'failures') -Filter *.json -EA SilentlyContinue).Count
    $req = $null
    $isComplete = $false
    $idx = [int]($_.Name -replace 'batch_', '')
    $need = if ($idx -eq $totalBatches) { $lastRequested } else { ($batchPosts * $repeats) }
    if (Test-Path $sum) {
      try {
        $j = Get-Content $sum -Raw | ConvertFrom-Json
        $req = [int]$j.total_requested_samples
        $isComplete = ($req -eq $need)
      } catch {}
    }
    if ($isComplete) { $complete++ } else { $incomplete += $_.Name }
    $ok += $s
    $fail += $f
    $batches += [pscustomobject]@{ name = $_.Name; complete = $isComplete; ok = $s; fail = $f; requested = $req }
  }

$workers = Get-Workers
$lm = Test-Lm
$cFree = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
$dFree = [math]::Round((Get-PSDrive D).Free / 1GB, 1)
$stderrTail = @(Get-Content (Join-Path $Campaign 'full.stderr.log') -Tail 3 -EA SilentlyContinue)
$lastLog = $null
if ($stderrTail.Count) {
  $raw = [string]($stderrTail | Select-Object -Last 1)
  if ($raw.Length -gt 240) { $raw = $raw.Substring(0, 240) + '…' }
  $lastLog = $raw
}
$genDone = ($complete -ge $totalBatches)
$zlgWorkers = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe', 'uv.exe') -and
    $_.CommandLine -match 'run_zlg_batch_comparison|zlg_lucid_fresh_6x'
  }).Count
$phase = if ($zlgWorkers -gt 0) { 'zlg_capacity_matched' }
  elseif ($genDone) { 'generation_complete' }
  else { 'generation_full' }

$status = [ordered]@{
  utc               = (Get-Date).ToUniversalTime().ToString('o')
  phase             = $phase
  workers           = $workers.Count
  zlg_workers       = $zlgWorkers
  lm_studio         = $lm
  batches_complete  = $complete
  batches_total     = $totalBatches
  incomplete        = $incomplete
  accounted_ok      = $ok
  accounted_fail    = $fail
  accounted_total   = ($ok + $fail)
  free_gb_c         = $cFree
  free_gb_d         = $dFree
  needs_resume      = ((-not $genDone) -and ($workers.Count -eq 0) -and ($zlgWorkers -eq 0))
  generation_done   = $genDone
  last_log          = $lastLog
}

$status | ConvertTo-Json -Depth 4 -Compress
if (Test-Path $StatePath) {
  try {
    $state = Get-Content $StatePath -Raw | ConvertFrom-Json
    $state.last_check_utc = $status.utc
    $state.phase = $status.phase
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding utf8
  } catch {}
}
