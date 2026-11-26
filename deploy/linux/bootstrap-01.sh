#!/usr/bin/env bash
set -euo pipefail
runtime="${RESEARCH_RUNTIME:-$HOME/research-runtime}"
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
uv="${UV_BIN:-uv}"
mkdir -p "$runtime/engines/01" "$runtime/cache/uv"
export UV_CACHE_DIR="$runtime/cache/uv"
export UV_PYTHON_INSTALL_DIR="$runtime/python"
"$uv" venv --python 3.11 "$runtime/engines/01/.venv"
"$uv" pip sync --python "$runtime/engines/01/.venv/bin/python" --torch-backend cpu --require-hashes "$repo/deploy/linux/requirements-01-linux-py311-cpu.lock" > "$runtime/engines/01/install.log" 2>&1
"$uv" pip check --python "$runtime/engines/01/.venv/bin/python"
"$runtime/engines/01/.venv/bin/python" -c 'import torch; print({"host_torch":torch.__version__,"cuda_runtime":torch.version.cuda})'
