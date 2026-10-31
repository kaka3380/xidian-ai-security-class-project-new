#!/usr/bin/env bash
set -euo pipefail
runtime="$HOME/research-runtime"
repo=/mnt/e/work/ai-security-class-project
uv="$runtime/tools/uv-0.12.9/uv"
mkdir -p "$runtime/engines/01" "$runtime/cache/uv"
export UV_CACHE_DIR="$runtime/cache/uv"
export UV_PYTHON_INSTALL_DIR="$runtime/python"
"$uv" pip compile "$repo/projects/project-01-ai-researcher/setup.cfg" --python "$runtime/engines/02/.venv/bin/python" --torch-backend cpu --generate-hashes --output-file "$runtime/engines/01/requirements-cpu.lock" > "$runtime/engines/01/resolve.log" 2>&1
cp "$runtime/engines/01/requirements-cpu.lock" "$repo/deploy/linux/requirements-01-linux-py311-cpu.lock"
"$uv" venv --python "$runtime/engines/02/.venv/bin/python" "$runtime/engines/01/.venv"
"$uv" pip sync --python "$runtime/engines/01/.venv/bin/python" --torch-backend cpu --require-hashes "$runtime/engines/01/requirements-cpu.lock" > "$runtime/engines/01/install.log" 2>&1
"$uv" pip check --python "$runtime/engines/01/.venv/bin/python"
"$runtime/engines/01/.venv/bin/python" -c 'import torch; print({"host_torch":torch.__version__,"cuda_runtime":torch.version.cuda})'
