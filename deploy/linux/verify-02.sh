#!/usr/bin/env bash
set -euo pipefail
repo=${1:?Usage: verify-02.sh /mnt/e/path/to/repo}
engine="$HOME/research-runtime/engines/02"
uv="$HOME/research-runtime/tools/uv-0.12.9/uv"
python="$engine/.venv/bin/python"
evidence="$repo/deploy/linux/evidence"
mkdir -p "$evidence"
[[ $(id -u) != 0 ]]
source_dir=$(cat "$engine/source-path.txt")
cat /etc/os-release > "$evidence/ubuntu-os.txt"
uname -a > "$evidence/ubuntu-kernel.txt"
id > "$evidence/ubuntu-user.txt"
/usr/lib/wsl/lib/nvidia-smi > "$evidence/ubuntu-nvidia-smi.txt"
"$uv" --version > "$evidence/uv-version.txt"
"$python" "$repo/deploy/linux/gpu_probe.py" > "$evidence/ubuntu-gpu.json"
"$uv" pip check --python "$python" > "$evidence/dependency-check.txt" 2>&1
"$uv" pip freeze --python "$python" > "$evidence/installed-02.txt"
cd "$source_dir"
PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true HF_HUB_OFFLINE=1 WANDB_MODE=disabled \
    "$python" launch_scientist_bfts.py --help > "$evidence/entrypoint-help.txt" 2>&1
docker version --format '{{json .}}' > "$evidence/docker-version.json"
image=$(docker image inspect research/scientist02:stage-a-20261022 --format '{{.Id}}')
docker image inspect "$image" --format '{{json .Id}}' > "$evidence/image-id.json"
docker image inspect "$image" \
    --format '{"id":{{json .Id}},"size_bytes":{{.Size}},"user":{{json .Config.User}},"repo_digests":{{json .RepoDigests}}}' \
    > "$evidence/container-image.json"
docker run --rm --name scientist02-stage-a-gpu --label research.stage=A \
    --gpus all --cpus 4 --memory 4g --memory-swap 4g --pids-limit 256 \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m "$image" > "$evidence/container-gpu.json"
docker run --rm --name scientist02-stage-a-import --label research.stage=A \
    --cpus 2 --memory 2g --memory-swap 2g --pids-limit 128 \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m "$image" \
    python /opt/scientist02/launch_scientist_bfts.py --help \
    > "$evidence/container-entrypoint-help.txt" 2>&1
sha256sum "$repo/deploy/linux/requirements-02-linux-py311.lock" \
    > "$evidence/requirements-lock.sha256"
"$python" - "$evidence" <<'PY'
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

evidence = Path(sys.argv[1])
read_json = lambda name: json.loads((evidence / name).read_text(encoding="utf-8-sig"))
ubuntu = read_json("ubuntu-gpu.json")
container = read_json("container-gpu.json")
assert ubuntu["status"] == container["status"] == "passed"
assert re.search(r"uid=([1-9][0-9]*)", (evidence / "ubuntu-user.txt").read_text())
for name in ("entrypoint-help.txt", "container-entrypoint-help.txt"):
    assert "usage: launch_scientist_bfts.py" in (evidence / name).read_text()
assert "All installed packages are compatible" in (evidence / "dependency-check.txt").read_text()
image = read_json("container-image.json")
assert image["user"] == "1000:1000"
summary = {
    "stage": "A", "status": "passed",
    "recorded_at": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    "model_requests": 0,
    "installed_packages": len((evidence / "installed-02.txt").read_text().splitlines()),
    "requirements_sha256": hashlib.sha256((evidence.parent / "requirements-02-linux-py311.lock").read_bytes()).hexdigest(),
    "source": read_json("source-02.json"),
    "image": image, "ubuntu_gpu": ubuntu, "container_gpu": container,
    "checks": {"ordinary_user": "passed", "dependency_check": "passed",
               "ubuntu_entrypoint": "passed", "container_entrypoint": "passed"},
}
(evidence / "acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
echo 'Stage A GPU/dependency/container acceptance passed; model requests: 0.'
