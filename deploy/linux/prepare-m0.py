"""Prepare a new offline M0 evidence lock or reproducible image context (no Docker/model calls)."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from executor.protocol import canonical, confined, digest, file_hash
from workbench.bridges.scientist02.journal import freeze_journal_inputs


JOURNAL_ROOT = "outputs/02-functional-check-20261024/artifacts/summary-input"
HISTORY = [
    "outputs/02-functional-check-20261024/README.md",
    "outputs/02-functional-check-20261024/artifacts/summary-result.json",
    "outputs/02-functional-check-20261024/artifacts/draft-summary.json",
    "outputs/02-functional-check-20261024/artifacts/baseline-summary.json",
    "outputs/02-functional-check-20261024/artifacts/research-summary.json",
    "outputs/02-functional-check-20261024/artifacts/ablation-summary.json",
    "outputs/02-functional-check-20261024/artifacts/paper/provenance.json",
    "outputs/02-local-walkthrough/scientist-runtime/runs/attempt-06/status.json",
    "outputs/02-local-walkthrough/scientist-runtime/runs/attempt-06/source-overlay.json",
    "outputs/02-local-walkthrough/scientist-runtime/runs/attempt-06/engine.log",
]
PATCHES = ["launch_scientist_bfts.py", "ai_scientist/treesearch/agent_manager.py",
           "ai_scientist/treesearch/utils/config.py", "ai_scientist/perform_icbinb_writeup.py"]
BRIDGE = ["executor/protocol.py", "workbench/backend/services/stage_result.py",
          "workbench/bridges/scientist02/journal.py", "workbench/bridges/scientist02/replay.py"]


def freeze(output):
    manifest = {"schema_version": 1, "kind": "scientist02.m0-evidence", "journal_root": JOURNAL_ROOT,
                "journal_inputs": freeze_journal_inputs(ROOT / JOURNAL_ROOT),
                "evidence_files": [{"path": path, "sha256": file_hash(ROOT / path)} for path in HISTORY]}
    manifest["evidence_hash"] = digest(canonical(manifest))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(canonical(manifest))
    return {"evidence_lock": str(output), "evidence_hash": manifest["evidence_hash"]}


def prepare(output):
    base = json.loads((ROOT / "deploy/linux/m0/base-image.json").read_text(encoding="utf-8"))
    image = base["image_id"]
    if image != "sha256:dd847afa7febd6c25f1d4ef06e1ef788d7ecdae504760849e07e9780d66569be":
        raise ValueError("unexpected base image; audit a new base explicitly")
    output.mkdir(parents=True, exist_ok=False)
    files = []

    def copy(source, destination, target):
        data = confined(ROOT, source).read_bytes()
        path = confined(output, destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files.append({"path": source, "context_path": destination, "target": target, "sha256": digest(data)})

    for path in PATCHES:
        copy("projects/project-02-ai-scientist-v2/" + path, "source/" + path, "/opt/scientist02/" + path)
    for path in BRIDGE:
        copy(path, "bridge/" + path, "/opt/" + path)
    copy("deploy/linux/m0/runtime_probe.py", "runtime_probe.py", "/opt/m0/runtime_probe.py")
    copy("deploy/linux/m0/Dockerfile", "Dockerfile", None)
    lock = {"schema_version": 1, "base_image_id": image, "files": files}
    lock["source_snapshot_hash"] = digest(canonical(lock))
    (output / "source-lock.json").write_bytes(canonical(lock))
    return {"context": str(output), "source_snapshot_hash": lock["source_snapshot_hash"], "files": len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "prepare"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps((freeze if args.action == "freeze" else prepare)(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
