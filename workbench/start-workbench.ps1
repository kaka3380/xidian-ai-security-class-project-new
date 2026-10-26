#Requires -Version 7.0
[CmdletBinding()]
param([switch]$Foreground, [int]$Port = 8090)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo 'projects\project-04-Intelligence Gathering\.venv\Scripts\python.exe'
$data = Join-Path $PSScriptRoot 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null
if (-not (Test-Path -LiteralPath $python)) { throw 'Use the existing project04 environment; Python not found.' }
if ($Foreground) {
    Push-Location -LiteralPath $repo
    try { & $python -m uvicorn workbench.backend.api.app:create_app --factory --host 127.0.0.1 --port $Port }
    finally { Pop-Location }
    return
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port is already listening; inspect the existing service first." }
$api = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','workbench.backend.api.app:create_app','--factory','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $data 'api.stdout.log') -RedirectStandardError (Join-Path $data 'api.stderr.log')
$worker = Start-Process -FilePath $python -ArgumentList @('-m','workbench.worker') -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $data 'worker.stdout.log') -RedirectStandardError (Join-Path $data 'worker.stderr.log')
@{api_pid=$api.Id;worker_pid=$worker.Id;url="http://127.0.0.1:$Port/workbench";started_at=[DateTimeOffset]::Now.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $data 'processes.json') -Encoding utf8
Write-Output "http://127.0.0.1:$Port/workbench"
