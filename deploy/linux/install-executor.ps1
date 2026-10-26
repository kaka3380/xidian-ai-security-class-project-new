$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if ($repo -ne 'E:\work\ai-security-class-project') { throw 'This deployment targets the configured E: workspace.' }
$source = '/mnt/e/work/ai-security-class-project/executor'
$target = '/home/research/research-runtime/executor'
& wsl.exe -d Ubuntu-24.04 -u research --exec /usr/bin/install -d $target
if ($LASTEXITCODE -ne 0) { throw 'Cannot create executor directory' }
foreach ($name in @('protocol.py', 'main.py', 'container_tasks.py')) {
    & wsl.exe -d Ubuntu-24.04 -u research --exec /usr/bin/install -m 644 "$source/$name" "$target/$name"
    if ($LASTEXITCODE -ne 0) { throw "Cannot install $name" }
}
$files = @(
    'workbench/backend/services/review_workflow.py',
    'workbench/backend/services/research_package.py',
    'workbench/adapters/scientist02.py'
)
foreach ($file in $files) {
    $destination = '/home/research/research-runtime/' + $file
    $parent = $destination.Substring(0, $destination.LastIndexOf('/'))
    & wsl.exe -d Ubuntu-24.04 -u research --exec /usr/bin/install -d $parent
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create runtime module directory' }
    & wsl.exe -d Ubuntu-24.04 -u research --exec /usr/bin/install -m 644 ('/mnt/e/work/ai-security-class-project/' + $file) $destination
    if ($LASTEXITCODE -ne 0) { throw "Cannot install $file" }
}
& wsl.exe -d Ubuntu-24.04 -u research --exec /usr/bin/install -d /home/research/research-runtime/support
& wsl.exe -d Ubuntu-24.04 -u research --exec /usr/bin/install -m 644 /mnt/e/work/ai-security-class-project/deploy/linux/frozen_experiment.py /home/research/research-runtime/support/frozen_experiment.py
if ($LASTEXITCODE -ne 0) { throw 'Cannot install experiment support' }
& wsl.exe -d Ubuntu-24.04 -u research --exec /home/research/research-runtime/engines/02/.venv/bin/python "$target/main.py" probe
if ($LASTEXITCODE -ne 0) { throw 'Executor probe failed' }
