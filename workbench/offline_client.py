"""Windows handoff client. All WSL invocations use fixed executable argument lists."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from executor.protocol import canonical, confined, digest, file_hash, identifier, local_path, snapshot_hash, validate_package

REPO = local_path(Path(__file__).resolve().parent.parent)
EXCHANGE = REPO / "workbench/exchange"
DATA = REPO / "workbench/data/experiments"
LINUX_PYTHON = "/home/research/research-runtime/engines/02/.venv/bin/python"
LINUX_EXECUTOR = "/home/research/research-runtime/executor/main.py"


def publish_directory(staging, target):
    # Windows scanners can briefly hold a newly written directory open.
    for attempt in range(20):
        try:
            os.rename(staging, target)
            return
        except PermissionError:
            if os.name != "nt" or target.exists() or attempt == 19:
                raise
            time.sleep(0.05)


def invoke(operation, task=None, project=None):
    if operation not in {"probe", "submit", "status", "cancel", "collect"}:
        raise ValueError("unsupported operation")
    args = ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "research", "--exec", LINUX_PYTHON, LINUX_EXECUTOR, operation]
    if operation != "probe":
        args.extend([identifier(task), identifier(project)])
    completed = subprocess.run(args, capture_output=True, timeout=30)
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("WSL executor returned no valid JSON; check Ubuntu availability") from error
    if completed.returncode:
        raise ValueError(result.get("error", "executor failure"))
    return result


def create_mock_package(task, *, project="offline-demo", duration=0.2, timeout=10, mode="success", attempt=1):
    identifier(task)
    identifier(project)
    EXCHANGE.mkdir(parents=True, exist_ok=True)
    folder = EXCHANGE / task
    staging = Path(tempfile.mkdtemp(prefix=".package-", dir=EXCHANGE))
    try:
        (staging / "inputs").mkdir()
        payload = b"Synthetic offline fixture. No external research evidence.\n"
        (staging / "inputs/evidence.txt").write_bytes(payload)
        documents = [{"document_id": "fixture", "version": 1, "sha256": digest(payload), "content_level": "abstract", "path": "inputs/evidence.txt"}]
        config = {"duration_seconds": duration, "timeout_seconds": timeout, "mode": mode}
        plan = {"question": "Does the offline protocol return verified bytes?", "hypothesis": "Returned hashes match", "method": "mock byte count", "dataset_id": "synthetic-fixture", "dataset_version": 1, "dataset_hash": digest(payload), "baseline": "input byte length", "metrics": ["input_bytes"], "seeds": [0]}
        package = dict(plan, schema_version=1, purpose="offline_protocol_test", project_id=project, task_id=task, attempt=attempt, engine="mock", documents=documents, approved_manifest_hash=digest(canonical(documents)), plan_version=1, plan_hash=digest(canonical(plan)), approval={"reviewer": "offline-fixture", "reviewed_at": "2026-10-22T00:00:00Z", "synthetic": True}, config=config, config_hash=digest(canonical(config)), source_snapshot_hash=snapshot_hash(), image=None)
        (staging / "research_package.json").write_bytes(canonical(package))
        validate_package(package, staging)
        if folder.exists():
            raise ValueError("task exchange already exists")
        publish_directory(staging, folder)
        return package
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def collect_verified(task, project):
    receipt = invoke("collect", task, project)
    source = confined(EXCHANGE, identifier(task))
    package = json.loads((source / "research_package.json").read_text(encoding="utf-8"))
    real = package.get("schema_version") == 2
    if real:
        from workbench.backend.services.research_package import validate_frozen_package
        document = json.loads((source / "approved-document.json").read_text(encoding="utf-8"))
        plan = json.loads((source / "approved-plan.json").read_text(encoding="utf-8"))
        files = {doc["path"]: confined(source, doc["path"]).read_bytes() for doc in package["documents"]}
        validate_frozen_package(package, files, documents=[document], plan=plan)
        package_hash = package["package_hash"]
    else:
        package_hash = validate_package(package, source, require_current_source=False)
    result_folder = confined(source, "result")
    result_path = confined(result_folder, "result.json")
    if file_hash(result_path) != receipt["result_sha256"]:
        raise ValueError("result hash mismatch")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if any(result[k] != expected for k, expected in (("task_id", task), ("project_id", project), ("package_hash", package_hash), ("engine", package["engine"]))):
        raise ValueError("result ownership/input mismatch")
    if receipt["package_hash"] != package_hash or receipt["task_id"] != task or receipt["project_id"] != project:
        raise ValueError("receipt mismatch")
    if result["validation_status"] == "passed" and (result["execution_status"] != "completed" or not all(m in result["metrics"] for m in package["metrics"])):
        raise ValueError("missing required metrics")
    parent = DATA / identifier(project)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / task
    staging = Path(tempfile.mkdtemp(prefix=".receive-", dir=parent))
    try:
        seen = set()
        for artifact in result["artifacts"]:
            name = artifact["path"]
            allowed = name.startswith("experiments/") or name in {"engine.log", "ideas.json", "ideas.py", "bfts_config.yaml", "adapter.json", "dataset.npz"} if real else name.startswith("artifacts/")
            if not allowed or name in seen:
                raise ValueError("invalid artifact path")
            seen.add(name)
            artifact_source = confined(result_folder, name)
            artifact_target = confined(staging, name)
            artifact_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact_source, artifact_target)
            if file_hash(artifact_target) != artifact["sha256"]:
                raise ValueError("artifact hash mismatch")
        (staging / "result.json").write_bytes(canonical(result))
        (staging / "research_package.json").write_bytes(canonical(package))
        if real:
            for name, value in (("approved-document.json", document), ("approved-plan.json", plan)):
                (staging / name).write_bytes(canonical(value))
            for name, data in files.items():
                target_input = confined(staging, name)
                target_input.parent.mkdir(parents=True, exist_ok=True)
                target_input.write_bytes(data)
        if target.exists():
            for path in staging.rglob("*"):
                if path.is_file() and file_hash(confined(target, path.relative_to(staging).as_posix())) != file_hash(path):
                    raise ValueError("existing local record differs")
        else:
            publish_directory(staging, target)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)
