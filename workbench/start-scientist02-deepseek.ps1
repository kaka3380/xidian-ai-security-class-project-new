#Requires -Version 7.0
<#
.SYNOPSIS
Prepare a small AI-Scientist-v2 experiment, or run it through the existing DeepSeek gateway.
.DESCRIPTION
Without -Run this only writes an isolated configuration and example idea. With -Run,
model calls are charged to the supplied DeepSeek key and use the existing gateway budget guard.
This first experiment skips paper writing and review.
#>
[CmdletBinding()]
param(
    [string]$KeyFile,
    [string]$IdeasFile,
    [string]$OutputDirectory,
    [switch]$Run
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$image = 'research/scientist02:m0-20261024'
$alias = 'gpt-4o-2024-11-20' # Local compatibility name; the gateway sends deepseek-flash upstream.
$python = Join-Path $repo 'projects\project-04-Intelligence Gathering\.venv\Scripts\python.exe'
if ($IdeasFile) {
    $ideasPath = (Resolve-Path -LiteralPath $IdeasFile).Path
    $ideas = Get-Content -LiteralPath $ideasPath -Raw | ConvertFrom-Json -AsHashtable -NoEnumerate
    if ($ideas -isnot [array] -or $ideas.Count -eq 0) { throw 'IdeasFile must contain a non-empty JSON array.' }
    foreach ($idea in $ideas) {
        if ($idea -isnot [System.Collections.IDictionary] -or $idea.Name -isnot [string] -or $idea.Name -notmatch '^[A-Za-z0-9_-]+$' -or -not $idea.Title) {
            throw 'Each idea needs an ASCII Name using letters/numbers/underscore/hyphen, and a Title.'
        }
    }
}
if ($Run) {
    if (-not $KeyFile -or -not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) {
        throw 'Use -KeyFile with an existing local file containing your DeepSeek API key. Do not put the key in the command.'
    }
    $keyPath = (Resolve-Path -LiteralPath $KeyFile).Path
    if (-not (Test-Path -LiteralPath $python)) { throw 'The existing project04 Python environment was not found.' }
    docker image inspect $image --format '{{.Id}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and confirm that the Scientist02 image exists.' }
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repo ('outputs\scientist02-deepseek\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6))
}
$folder = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $folder) { throw 'OutputDirectory must be a new directory; existing experiments are never overwritten.' }
New-Item -ItemType Directory -Path $folder | Out-Null
$config = Get-Content -LiteralPath (Join-Path $repo 'projects\project-02-ai-scientist-v2\bfts_config.yaml') -Raw
# Prepare through the existing image without network or API credentials.
$prepareCode = @'
import json, sys
from pathlib import Path
import yaml
root = Path(sys.argv[1])
cfg = yaml.safe_load(sys.stdin.read())
alias = 'gpt-4o-2024-11-20'
cfg['agent']['num_workers'] = 1
cfg['agent']['steps'] = 1
cfg['agent']['stages'] = {f'stage{i}_max_iters': n for i, n in enumerate((2, 2, 1, 1), 1)}
cfg['agent']['multi_seed_eval']['num_seeds'] = 1
cfg['agent']['search'].update(num_drafts=1, max_debug_depth=1)
cfg['exec']['timeout'] = 180
for role in ('code', 'feedback', 'vlm_feedback'):
    cfg['agent'][role].update(model=alias, max_tokens=8192)
for role in ('summary', 'select_node'):
    cfg['agent'][role] = dict(model=alias, temp=0.3, max_tokens=4096)
cfg['report']['model'] = alias
(root / 'bfts_config.yaml').write_text(json.dumps(cfg, indent=2), encoding='utf-8')
idea = {
    'Name': 'small_synthetic_regularization',
    'Title': 'Small synthetic binary classification: compare weight decay strengths',
    'Short Hypothesis': 'Mild L2 regularization may improve held-out accuracy of a tiny MLP on noisy synthetic data.',
    'Related Work': 'An environment and workflow demonstration; no novelty or literature claims.',
    'Abstract': 'Compare weight decay 0, 0.001 and 0.01 on a fixed, locally generated binary classification dataset using the same small MLP and data splits.',
    'Experiments': 'Use only installed numpy, torch and matplotlib. Generate 512 samples with 8 features and noisy linear labels at seed 42. Fix train/validation/test splits at 60/20/20 percent. Use one hidden layer of width 16, batch size 32, at most 10 epochs and one seed. Compare all weight decay values with identical architecture and initialization. Save test accuracy, validation loss, settings, and plots. All stages must reuse these synthetic data; do not download HuggingFace datasets, pretrained models, or other network resources. Do not pip install packages. Keep experiments within 6GB VRAM.',
    'Risk Factors and Limitations': 'A small synthetic smoke experiment, not evidence of scientific novelty or real-world effectiveness. Stop and report missing results rather than invent them.'
}
(root / 'ideas.json').write_text(json.dumps([idea], indent=2), encoding='utf-8')
'@
$config | docker run --rm -i --pull never --network none --read-only --tmpfs /tmp:rw,size=64m `
    --cap-drop ALL --security-opt no-new-privileges --memory 256m --cpus 1 `
    --mount "type=bind,source=$folder,target=/prepare" --entrypoint python $image -c $prepareCode /prepare
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the isolated configuration; inspect the Docker output above.' }
if ($IdeasFile) { Copy-Item -LiteralPath $ideasPath -Destination (Join-Path $folder 'ideas.json') }
Write-Output "Prepared: $folder"
Write-Output 'Input: ideas.json; configuration: bfts_config.yaml; experiment stages: 2/2/1/1; paper writing and review: skipped.'
if (-not $Run) {
    Write-Output 'Preparation only. No model API calls were made. Use -KeyFile <local-path> -Run to execute the example, or add -IdeasFile <edited-ideas.json> to use your own topic.'
    return
}
$gatewayDir = Join-Path $folder 'gateway'
New-Item -ItemType Directory -Path $gatewayDir | Out-Null
$portReservation = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$portReservation.Start()
$port = $portReservation.LocalEndpoint.Port
$portReservation.Stop()
$gateway = $null
$oldApiKey = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'Process')
$oldBaseUrl = [Environment]::GetEnvironmentVariable('OPENAI_BASE_URL', 'Process')
try {
    $arguments = @('-m', 'workbench.backend.services.model_gateway', '--key-file', ('"' + $keyPath + '"'),
        '--runtime', ('"' + $gatewayDir + '"'), '--host', '127.0.0.1', '--port', "$port")
    $gateway = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $gatewayDir 'stdout.log') -RedirectStandardError (Join-Path $gatewayDir 'stderr.log')
    $pointer = Join-Path $gatewayDir 'gateway.json'
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $pointer)) {
        if ($gateway.HasExited) { throw "Gateway failed to start. Inspect $gatewayDir\stderr.log" }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Gateway startup timed out.' }
        Start-Sleep -Milliseconds 100
    }
    $settings = Get-Content -LiteralPath $pointer -Raw | ConvertFrom-Json
    $env:OPENAI_API_KEY = $settings.token
    $env:OPENAI_BASE_URL = "http://host.docker.internal:$port/v1"
    $connectionProbe = @'
import json, os, urllib.request
req = urllib.request.Request(os.environ['OPENAI_BASE_URL'] + '/models', headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as response:
    models = json.load(response)
assert any(m['id'] == 'deepseek-flash' for m in models['data'])
print('Container-to-gateway connection verified; model requests have not started yet.')
'@
    docker run --rm --pull never --env OPENAI_API_KEY --env OPENAI_BASE_URL --entrypoint python $image -c $connectionProbe
    if ($LASTEXITCODE -ne 0) { throw 'The container cannot reach the local gateway. No experiment was started.' }
    Write-Output 'Starting paid DeepSeek experiment through the existing gateway budget guard. Output: experiment.log and experiments/.'
    docker run --rm --pull never --gpus all --read-only --tmpfs /tmp:rw,size=256m `
        --cap-drop ALL --security-opt no-new-privileges --memory 3g --cpus 2 `
        --env OPENAI_API_KEY --env OPENAI_BASE_URL --env NO_PROXY=host.docker.internal,127.0.0.1,localhost `
        --env MPLCONFIGDIR=/tmp/matplotlib-config --env XDG_CACHE_HOME=/tmp/xdg-cache `
        --workdir /task --mount "type=bind,source=$folder,target=/task" --entrypoint python $image `
        /opt/scientist02/launch_scientist_bfts.py --load_ideas /task/ideas.json --idea_idx 0 `
        --skip_writeup --skip_review --model_agg_plots $alias 2>&1 | Tee-Object -FilePath (Join-Path $folder 'experiment.log')
    if ($LASTEXITCODE -ne 0) { throw "Experiment failed. Inspect $folder\experiment.log; no automatic retry was made." }
    Write-Output "Experiment process finished. Inspect artifacts in $folder\experiments; process completion alone does not validate research conclusions."
}
finally {
    [Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $oldApiKey, 'Process')
    [Environment]::SetEnvironmentVariable('OPENAI_BASE_URL', $oldBaseUrl, 'Process')
    if ($gateway -and -not $gateway.HasExited) {
        Stop-Process -Id $gateway.Id -ErrorAction SilentlyContinue
    }
    # This is a local proxy token, not the provider key. Remove it after the gateway exits.
    Remove-Item -LiteralPath (Join-Path $gatewayDir 'gateway.json') -ErrorAction SilentlyContinue
}
