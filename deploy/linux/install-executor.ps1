[CmdletBinding()]
param(
    [string]$Distribution = $env:WORKBENCH_WSL_DISTRO,
    [string]$LinuxUser = $env:WORKBENCH_WSL_USER,
    [string]$Runtime = $env:WORKBENCH_WSL_RUNTIME
)
$ErrorActionPreference = 'Stop'
if ($Distribution -notmatch '^[A-Za-z0-9][A-Za-z0-9_. -]*$' -or
    $LinuxUser -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*$' -or
    $Runtime -notmatch '^/[^\r\n]+$' -or ($Runtime -split '/') -contains '..' -or ($Runtime -split '/') -contains '.') {
    throw 'Set the WSL distribution, Linux user and an absolute Linux runtime path.'
}
$Runtime = $Runtime.TrimEnd('/')
$wslArgs = @('-d', $Distribution, '-u', $LinuxUser, '--exec')
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$linuxRepo = & wsl.exe @wslArgs wslpath -a $repo
if ($LASTEXITCODE -ne 0 -or -not $linuxRepo) { throw 'Cannot resolve the repository path in Ubuntu WSL' }
$linuxRepo = $linuxRepo.TrimEnd('/')
$source = "$linuxRepo/executor"
$target = "$Runtime/executor"
& wsl.exe @wslArgs /usr/bin/install -d $target
if ($LASTEXITCODE -ne 0) { throw 'Cannot create executor directory' }
foreach ($name in @('protocol.py', 'main.py', 'container_tasks.py', 'host.py')) {
    & wsl.exe @wslArgs /usr/bin/install -m 644 "$source/$name" "$target/$name"
    if ($LASTEXITCODE -ne 0) { throw "Cannot install $name" }
}
$files = @(
    'workbench/backend/services/review_workflow.py',
    'workbench/backend/services/research_package.py',
    'workbench/backend/services/injection_experiment.py',
    'workbench/backend/services/experiment_budget.py',
    'workbench/backend/services/model_gateway.py',
    'workbench/bridges/scientist02/injection_runner.py',
    'workbench/adapters/scientist02.py'
)
foreach ($file in $files) {
    $destination = "$Runtime/$file"
    $parent = $destination.Substring(0, $destination.LastIndexOf('/'))
    & wsl.exe @wslArgs /usr/bin/install -d $parent
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create runtime module directory' }
    & wsl.exe @wslArgs /usr/bin/install -m 644 "$linuxRepo/$file" $destination
    if ($LASTEXITCODE -ne 0) { throw "Cannot install $file" }
}
& wsl.exe @wslArgs /usr/bin/install -d "$Runtime/support"
if ($LASTEXITCODE -ne 0) { throw 'Cannot create support directory' }
& wsl.exe @wslArgs /usr/bin/install -m 644 "$linuxRepo/deploy/linux/frozen_experiment.py" "$Runtime/support/frozen_experiment.py"
if ($LASTEXITCODE -ne 0) { throw 'Cannot install experiment support' }
& wsl.exe @wslArgs "$Runtime/engines/02/.venv/bin/python" "$target/main.py" probe
if ($LASTEXITCODE -ne 0) { throw 'Executor probe failed' }
