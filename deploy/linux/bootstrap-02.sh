#!/usr/bin/env bash
# Run as the ordinary WSL user, after installing ca-certificates/curl/libgomp1.
set -euo pipefail
umask 022
repo=${1:?Usage: bootstrap-02.sh /mnt/e/path/to/repo source_archive_sha256}
snapshot=${2:?Missing archive SHA-256}
[[ $snapshot =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid snapshot hash' >&2; exit 2; }
[[ $(id -u) != 0 ]] || { echo 'Run as an ordinary user' >&2; exit 2; }
runtime="$HOME/research-runtime"
engine="$runtime/engines/02"
source_dir="$engine/sources/$snapshot"
uv_version=0.12.9
uv_dir="$runtime/tools/uv-$uv_version"
export UV_CACHE_DIR="$runtime/cache/uv"
export UV_PYTHON_INSTALL_DIR="$runtime/python"
export UV_HTTP_TIMEOUT=120
export UV_CONCURRENT_DOWNLOADS=2
mkdir -p "$engine" "$runtime/runs" "$uv_dir"
if [[ ! -x "$uv_dir/uv" ]]; then
    temp=$(mktemp -d)
    trap 'rm -rf -- "$temp"' EXIT
    url="https://github.com/astral-sh/uv/releases/download/$uv_version/uv-x86_64-unknown-linux-gnu.tar.gz"
    curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 "$url" -o "$temp/uv.tar.gz"
    curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 "$url.sha256" -o "$temp/uv.sha256"
    expected=$(awk '{print $1}' "$temp/uv.sha256")
    [[ $expected =~ ^[a-f0-9]{64}$ ]]
    printf '%s  %s\n' "$expected" "$temp/uv.tar.gz" | sha256sum --check
    tar -xzf "$temp/uv.tar.gz" -C "$uv_dir" --strip-components=1
fi
uv="$uv_dir/uv"
"$uv" --version
if [[ ! -d "$source_dir" ]]; then
    archive="$repo/.cache/linux-stage-a/source-02.tar"
    printf '%s  %s\n' "$snapshot" "$archive" | sha256sum --check
    mkdir -p "$source_dir"
    tar -xf "$archive" -C "$source_dir" --strip-components=2
fi
"$uv" python install 3.11.16 --no-bin
if [[ ! -x "$engine/.venv/bin/python" ]]; then
    "$uv" venv --python 3.11.16 "$engine/.venv"
fi
cd "$repo/deploy/linux"
if [[ ! -f requirements-02-linux-py311.lock ]]; then
    "$uv" pip compile requirements-02.in --python "$engine/.venv/bin/python" \
        --torch-backend cu124 --generate-hashes \
        --output-file requirements-02-linux-py311.lock
fi
if [[ -f "$repo/.cache/linux-stage-a/wheelhouse/COMPLETE" ]]; then
    "$uv" pip sync --python "$engine/.venv/bin/python" --require-hashes \
        --no-index --find-links "$repo/.cache/linux-stage-a/wheelhouse" \
        requirements-02-linux-py311.lock
else
    "$uv" pip sync --python "$engine/.venv/bin/python" --require-hashes \
        --torch-backend cu124 requirements-02-linux-py311.lock
fi
"$uv" pip check --python "$engine/.venv/bin/python"
"$uv" pip freeze --python "$engine/.venv/bin/python" > "$engine/installed.txt"
printf '%s\n' "$source_dir" > "$engine/source-path.txt"
"$engine/.venv/bin/python" "$repo/deploy/linux/gpu_probe.py"
# --help imports the real entrypoint without creating a client or calling a model.
cd "$source_dir"
PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true HF_HUB_OFFLINE=1 WANDB_MODE=disabled \
    "$engine/.venv/bin/python" launch_scientist_bfts.py --help
