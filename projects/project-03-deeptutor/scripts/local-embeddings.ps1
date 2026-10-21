#Requires -Version 7.0
[CmdletBinding()]
param([ValidateSet('Start', 'Stop')][string]$Action = 'Start')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$server = Join-Path $PSScriptRoot 'local_embeddings.py'
$runtime = Join-Path $projectRoot 'data\user\runtime'
$pidFile = Join-Path $runtime 'local-embeddings.pid'

if ($Action -eq 'Stop') {
    if (Test-Path -LiteralPath $pidFile) {
        $serverPid = [int](Get-Content -LiteralPath $pidFile -Raw)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
        if ($process -and $process.CommandLine.Contains($server)) {
            # uv's Windows Python launcher may own a child interpreter.
            & "$env:SystemRoot\System32\taskkill.exe" /PID $serverPid /T /F > $null
            if ($LASTEXITCODE -ne 0) { throw 'Could not stop the local embedding process tree.' }
            Write-Output 'Local embedding service stopped.'
        }
        elseif ($process) {
            throw 'Embedding PID belongs to another process; refusing to stop it.'
        }
        Remove-Item -LiteralPath $pidFile
    }
    return
}

$configFile = Join-Path $projectRoot 'data\user\settings\local_embeddings.json'
if (-not (Test-Path -LiteralPath $configFile)) { return }
$config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
if (-not $config.enabled) { return }

function Test-EmbeddingService {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 2 -NoProxy
    }
    catch { return $false }
    if ($health.service -ne 'deeptutor-local-embeddings') {
        throw 'Port 8766 is occupied by another service.'
    }
    return $true
}

if (Test-EmbeddingService) { return }
New-Item -ItemType Directory -Path $runtime -Force > $null
$process = Start-Process -FilePath $python -ArgumentList @('"' + $server + '"') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtime 'local-embeddings.log') `
    -RedirectStandardError (Join-Path $runtime 'local-embeddings-error.log')
$process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($process.HasExited) { throw "Local embeddings exited; inspect $runtime\local-embeddings-error.log" }
    if (Test-EmbeddingService) {
        Write-Output 'Local embeddings ready at http://127.0.0.1:8766/v1/embeddings'
        return
    }
    Start-Sleep -Milliseconds 1000
}
throw "Local embeddings did not become ready; inspect $runtime\local-embeddings-error.log"
