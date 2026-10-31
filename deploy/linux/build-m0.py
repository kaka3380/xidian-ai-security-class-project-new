"""Build using a checked local parent; record layer identity and offline runtime QA."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from executor.protocol import canonical, confined, digest, file_hash
from workbench.bridges.scientist02.replay import validate_lock


def build(docker, context, output, tag, replay_folder):
    context = context.resolve()
    replay_folder = replay_folder.resolve()
    lock = json.loads((context / "source-lock.json").read_text(encoding="utf-8"))
    if lock["source_snapshot_hash"] != digest(canonical({k: v for k, v in lock.items() if k != "source_snapshot_hash"})):
        raise ValueError("source lock mismatch")
    for item in lock["files"]:
        if file_hash(confined(context, item["context_path"])) != item["sha256"]:
            raise ValueError("build context changed")
    base = json.loads((ROOT / "deploy/linux/m0/base-image.json").read_text(encoding="utf-8"))

    def inspect(image):
        # Read selected non-secret metadata; never inspect container environments.
        text = subprocess.check_output([docker, "image", "inspect", image, "--format", "{{json .Id}} {{json .RootFS.Layers}}"], text=True)
        image_id, layers = text.strip().split(" ", 1)
        return json.loads(image_id), json.loads(layers)

    parent_id, parent_layers = inspect(base["historical_tag"])
    if parent_id != base["image_id"] or parent_id != lock["base_image_id"]:
        raise ValueError("local base tag no longer matches approved immutable image")
    evidence_lock = json.loads((replay_folder / "evidence-lock.json").read_text(encoding="utf-8"))
    validate_lock(ROOT, evidence_lock)
    output.mkdir(parents=True, exist_ok=False)
    # Preserve the small exact build inputs outside ignored .cache for later audit.
    with tarfile.open(output / "source-overlay.tar", "w") as archive:
        for name in ["source-lock.json", *(item["context_path"] for item in lock["files"])]:
            archive.add(confined(context, name), arcname=name, recursive=False)
    # Legacy builder consumes the existing local image without a registry HEAD.
    # This is scoped to this subprocess, not a persisted Docker/user setting.
    environment = dict(os.environ, DOCKER_BUILDKIT="0")
    with (output / "build.log").open("wb") as stream:
        completed = subprocess.run([docker, "build", "--pull=false", "--network=none", "--tag", tag,
                                    "--file", str(context / "Dockerfile"), str(context)],
                                   env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
    if completed.returncode:
        raise RuntimeError(f"offline build failed ({completed.returncode}); see {output / 'build.log'}")
    image_id, layers = inspect(tag)
    if layers[:len(parent_layers)] != parent_layers:
        raise ValueError("built image does not inherit the checked immutable parent")
    # Only data is mounted. All runtime patches and bridge sources are baked in.
    command = [docker, "run", "--rm", "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--user=1000:1000", "--cpus=2", "--memory=3g", "--pids-limit=256",
               "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m", "--env", "MPLCONFIGDIR=/tmp/matplotlib",
               "--mount", f"type=bind,src={replay_folder / 'inputs'},dst=/inputs,readonly",
               "--mount", f"type=bind,src={replay_folder / 'evidence-lock.json'},dst=/manifest.json,readonly",
               image_id, "python", "/opt/m0/runtime_probe.py", "--inputs", "/inputs", "--manifest", "/manifest.json"]
    with (output / "runtime.log").open("wb") as errors:
        run = subprocess.run(command, stdout=subprocess.PIPE, stderr=errors, timeout=120)
    if run.returncode:
        (output / "runtime-stdout.log").write_bytes(run.stdout)
        raise RuntimeError(f"offline runtime probe failed ({run.returncode}); see {output / 'runtime.log'}")
    probe = json.loads(run.stdout)
    if probe["source_snapshot_hash"] != lock["source_snapshot_hash"] or len(probe.get("reconstructed_stages", [])) != 4:
        raise ValueError("runtime source or reconstruction identity mismatch")
    (output / "source-lock.json").write_bytes(canonical(lock))
    (output / "runtime-inventory.json").write_bytes(canonical(probe))
    report = {"schema_version": 1, "status": "passed", "image_id": image_id, "tag": tag,
              "base_image_id": parent_id, "base_layers_verified": True,
              "source_archive_sha256": file_hash(output / "source-overlay.tar"),
              "source_snapshot_hash": lock["source_snapshot_hash"], "runtime_source_hash": probe["runtime_source_hash"],
              "evidence_hash": evidence_lock["evidence_hash"], "model_requests": 0,
              "scope": "offline build, tools and real Node/Journal reconstruction only; real model stages not rerun"}
    (output / "acceptance.json").write_bytes(canonical(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--tag", default="research/scientist02:m0-20261024")
    args = parser.parse_args()
    print(json.dumps(build(args.docker, args.context, args.output, args.tag, args.replay), ensure_ascii=False))
