[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$Task,

    [ValidateRange(1, 5)]
    [int]$Agents = 3,

    [string]$WorkingDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,

    [ValidateSet('read-only', 'workspace-write')]
    [string]$Sandbox = 'read-only',

    [string]$Model,

    [string]$OutputFile,

    [switch]$Json,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$codex = Get-Command codex -ErrorAction Stop
$root = (Resolve-Path -LiteralPath $WorkingDirectory).Path
$maxThreads = $Agents + 1

$prompt = @"
$Task

Use a multi-agent workflow. Spawn exactly $Agents subagents for independent,
bounded parts of this task. Give each subagent a distinct responsibility.
Wait for every subagent to finish, verify their findings, and return one
consolidated result with file references where applicable. Keep write work
coordinated through the root agent to avoid conflicting edits.
"@

$codexArgs = @(
    'exec'
    '--cd', $root
    '--sandbox', $Sandbox
    '--config', "agents.max_threads=$maxThreads"
    '--config', 'agents.max_depth=1'
)

if ($Model) {
    $codexArgs += @('--model', $Model)
}
if ($Json) {
    $codexArgs += '--json'
}
if ($OutputFile) {
    $outputPath = if ([IO.Path]::IsPathRooted($OutputFile)) {
        [IO.Path]::GetFullPath($OutputFile)
    } else {
        [IO.Path]::GetFullPath((Join-Path $root $OutputFile))
    }
    $codexArgs += @('--output-last-message', $outputPath)
}

if ($DryRun) {
    [PSCustomObject]@{
        Executable = $codex.Source
        Arguments = $codexArgs
        Prompt = $prompt
    } | ConvertTo-Json -Depth 4
    exit 0
}

& $codex.Source @codexArgs $prompt
exit $LASTEXITCODE
