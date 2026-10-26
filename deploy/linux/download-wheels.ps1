# Run with PowerShell 7 on Windows; does not install Linux packages into Windows.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$cache = Join-Path $repo '.cache\linux-stage-a'
$wheelhouse = Join-Path $cache 'wheelhouse'
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null
$oldPipCache = $env:PIP_CACHE_DIR
$oldPipCheck = $env:PIP_DISABLE_PIP_VERSION_CHECK
$lock = Join-Path $PSScriptRoot 'requirements-02-linux-py311.lock'
try {
    $env:PIP_CACHE_DIR = Join-Path $cache 'pip-cache'
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    & python (Join-Path $PSScriptRoot 'recover_cached_wheels.py')
    if ($LASTEXITCODE -ne 0) { throw "Cache recovery failed: $LASTEXITCODE" }
    # The index's download-r2 CDN can stall on this network. The primary official
    # domain serves identical wheel bytes; verify them against the same lock.
    $allowed = [regex]::Matches((Get-Content -LiteralPath $lock -Raw), '--hash=sha256:([0-9a-f]{64})') |
        ForEach-Object { $_.Groups[1].Value }
    foreach ($wheel in @(
        'torch-2.6.0+cu124-cp311-cp311-linux_x86_64.whl',
        'torchvision-0.21.0+cu124-cp311-cp311-linux_x86_64.whl',
        'torchaudio-2.6.0+cu124-cp311-cp311-linux_x86_64.whl'
    )) {
        $destination = Join-Path $wheelhouse $wheel
        if ((Test-Path -LiteralPath $destination) -and
            ((Get-FileHash -LiteralPath $destination).Hash.ToLowerInvariant() -in $allowed)) { continue }
        $partial = "$destination.download"
        $url = 'https://download.pytorch.org/whl/cu124/' + $wheel.Replace('+', '%2B')
        & curl.exe --fail --location --continue-at - --retry 3 --retry-all-errors `
            --connect-timeout 15 --max-time 600 --output $partial $url
        if ($LASTEXITCODE -ne 0) { throw "Official wheel download failed: $wheel" }
        if ((Get-FileHash -LiteralPath $partial).Hash.ToLowerInvariant() -notin $allowed) {
            throw "Wheel hash is not in the dependency lock: $wheel"
        }
        Move-Item -LiteralPath $partial -Destination $destination -Force
    }
    & python -m pip download --dest $wheelhouse --require-hashes --no-deps `
        --only-binary=:all: --platform manylinux2014_x86_64 `
        --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 `
        --platform linux_x86_64 --python-version 3.11 --implementation cp `
        --abi cp311 --abi abi3 --abi none `
        --find-links $wheelhouse `
        --extra-index-url 'https://download.pytorch.org/whl/cu124' `
        -r $lock `
        --progress-bar off --timeout 60 --retries 2
    if ($LASTEXITCODE -ne 0) { throw "pip download failed: $LASTEXITCODE" }
    (Get-FileHash -LiteralPath $lock).Hash.ToLowerInvariant() |
        Set-Content -LiteralPath (Join-Path $wheelhouse 'COMPLETE') -Encoding utf8
} finally {
    $env:PIP_CACHE_DIR = $oldPipCache
    $env:PIP_DISABLE_PIP_VERSION_CHECK = $oldPipCheck
}
