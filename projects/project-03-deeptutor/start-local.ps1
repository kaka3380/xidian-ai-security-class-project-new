#Requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Foreground,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Missing .venv. Follow the source installation steps in README.md first.'
}

$launchArgs = @('-m', 'deeptutor_cli.main', 'start', '--home', $PSScriptRoot)
if ($Dev) { $launchArgs += '--dev' }
if (-not $Foreground) { $launchArgs += '--detach' }
if ($NoBrowser) { $launchArgs += '--no-browser' }

Push-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot 'scripts\local-embeddings.ps1') -Action Start
    & $python @launchArgs
    if ($LASTEXITCODE -ne 0) { throw "DeepTutor start failed (exit $LASTEXITCODE)." }
}
finally {
    Pop-Location
}
