#Requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Missing .venv. Follow the source installation steps in README.md first.'
}

Push-Location -LiteralPath $PSScriptRoot
try {
    & $python -m deeptutor_cli.main stop --home $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw "DeepTutor stop failed (exit $LASTEXITCODE)." }
    & (Join-Path $PSScriptRoot 'scripts\local-embeddings.ps1') -Action Stop
}
finally {
    Pop-Location
}
