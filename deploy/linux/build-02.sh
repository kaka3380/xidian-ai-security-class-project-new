#!/usr/bin/env bash
set -euo pipefail
repo=${1:?Usage: build-02.sh /mnt/e/path/to/repo}
runtime="$HOME/research-runtime"
context="$runtime/build/02-stage-a"
wheelhouse="$repo/.cache/linux-stage-a/wheelhouse"
lock="$repo/deploy/linux/requirements-02-linux-py311.lock"
[[ $(id -u) != 0 ]]
[[ $(cat "$wheelhouse/COMPLETE" | tr -d '\r\n' | sed 's/^\xef\xbb\xbf//') == "$(sha256sum "$lock" | cut -d' ' -f1)" ]] \
    || { echo 'Wheelhouse is incomplete or belongs to another lock' >&2; exit 2; }
mkdir -p "$context"
cp "$runtime/tools/uv-0.12.9/uv" "$context/uv"
cp "$lock" "$repo/deploy/linux/gpu_probe.py" "$context/"
cp "$repo/deploy/linux/Dockerfile.02" "$context/Dockerfile"
source_dir=$(cat "$runtime/engines/02/source-path.txt")
mkdir -p "$context/source"
# The source directory is an immutable, verified Git archive, not a live repo.
cp -a "$source_dir/." "$context/source/"
docker build --platform linux/amd64 --network none \
    --build-context "wheels=$wheelhouse" \
    --tag research/scientist02:stage-a-20261022 "$context"
