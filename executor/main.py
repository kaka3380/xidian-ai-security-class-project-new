"""Linux-only, stdlib executor. No model clients, shell execution or database access."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from protocol import canonical, confined, digest, file_hash, identifier, snapshot_hash, validate_package

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
EXCHANGE = Path("/mnt/e/work/ai-security-class-project/workbench/exchange")
TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path, value):
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


@contextlib.contextmanager
def locked(task):
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / (identifier(task) + ".lock")).open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        # Close, never explicit LOCK_UN: a supervisor inherits the same lock.
        yield stream.fileno()


def identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {"pid": pid, "start_ticks": stat[19], "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(state):
    process = state.get("process")
    return bool(process and identity(process["pid"]) == process)


def finish(folder, state, status, reason=None):
    package = read(folder / "research_package.json")
    artifacts = []
    metrics = {}
    if status == "completed":
        metrics = {"input_bytes": sum((folder / d["path"]).stat().st_size for d in package["documents"])}
        if package["config"]["mode"] == "missing_metrics":
            metrics = {}
        (folder / "artifacts").mkdir(exist_ok=True)
        atomic(folder / "artifacts/metrics.json", metrics)
        artifacts.append({"path": "artifacts/metrics.json", "sha256": file_hash(folder / "artifacts/metrics.json")})
    result = {
        "schema_version": 1, "project_id": state["project_id"], "task_id": state["task_id"],
        "attempt": package["attempt"], "package_hash": state["package_hash"], "engine": "mock",
        "execution_status": status, "validation_status": "passed" if status == "completed" and all(m in metrics for m in package["metrics"]) else "failed",
        "exit_code": 0 if status == "completed" else (1 if status == "failed" else None),
        "failure_reason": reason, "started_at": state.get("started_at"), "finished_at": now(),
        "effective_config": package["config"], "source_snapshot_hash": package["source_snapshot_hash"], "image": None,
        "metrics": metrics, "artifacts": artifacts, "usage": {"model_requests": 0, "cost": None},
        "record_status": "pending_review", "evidence_kind": "synthetic_protocol_test",
    }
    atomic(folder / "result.json", result)
    state.update(execution_status=status, finished_at=result["finished_at"], failure_reason=reason)
    atomic(folder / "state.json", state)
    return state


def reconcile(folder):
    state = read(folder / "state.json")
    if state["execution_status"] not in TERMINAL and not alive(state):
        # Never restart a lost task. Recover a completed result/state write window.
        result_path = folder / "result.json"
        if result_path.exists():
            result = read(result_path)
            state.update(execution_status=result["execution_status"], finished_at=result["finished_at"], failure_reason=result["failure_reason"])
            atomic(folder / "state.json", state)
        else:
            state = finish(folder, state, "interrupted", "supervisor missing; automatic replay disabled")
    return state


def owned(task, project):
    folder = RUNS / identifier(task)
    state = read(folder / "state.json")
    if state["project_id"] != identifier(project):
        raise ValueError("project ownership mismatch")
    return folder


def submit(task, project):
    source = confined(EXCHANGE, identifier(task))
    package = read(confined(source, "research_package.json"))
    if package["task_id"] != task or package["project_id"] != project:
        raise ValueError("package identity mismatch")
    package_hash = validate_package(package, source, require_current_source=False)
    with locked(task) as lock_fd:
        folder = RUNS / task
        if folder.exists():
            owned(task, project)
            state = reconcile(folder)
            if state["package_hash"] != package_hash:
                raise ValueError("task ID conflict; use a new task ID for a new attempt")
            return state
        staging = Path(tempfile.mkdtemp(prefix=".accept-", dir=RUNS))
        try:
            for doc in package["documents"]:
                target = staging / doc["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(confined(source, doc["path"]), target)
            atomic(staging / "research_package.json", package)
            validate_package(package, staging)  # Detect source mutation during copy.
            state = {"task_id": task, "project_id": project, "package_hash": package_hash, "execution_status": "accepted", "accepted_at": now(), "process": None}
            atomic(staging / "state.json", state)
            os.rename(staging, folder)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        try:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_supervise", task, project, str(lock_fd)], pass_fds=(lock_fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=folder, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "LANG": "C.UTF-8"})
            state["process"] = identity(child.pid)
        except Exception:
            finish(folder, state, "interrupted", "supervisor launch failed")
            raise
        return state


def supervise(task, project, lock_fd):
    # Inherited flock bridges Popen -> persisted process identity, including parent death.
    folder = owned(task, project)
    state = read(folder / "state.json")
    state.update(process=identity(os.getpid()), execution_status="running", started_at=now())
    atomic(folder / "state.json", state)
    os.close(lock_fd)
    started = time.monotonic()
    config = read(folder / "research_package.json")["config"]
    try:
        while True:
            elapsed = time.monotonic() - started
            with locked(task):
                if (folder / "cancel.json").exists():
                    finish(folder, state, "cancelled", "cancel requested")
                    return
                if elapsed >= config["timeout_seconds"]:
                    finish(folder, state, "timed_out", "supervisor deadline exceeded")
                    return
                if elapsed >= config["duration_seconds"]:
                    failed = config["mode"] == "fail"
                    finish(folder, state, "failed" if failed else "completed", "mock failure" if failed else None)
                    return
            time.sleep(0.05)
    except Exception as error:
        with locked(task):
            finish(folder, state, "failed", type(error).__name__)


def status(task, project):
    with locked(task):
        folder = owned(task, project)
        state = reconcile(folder)
        response = dict(state, process_alive=alive(state))
        if response["process_alive"] and state["execution_status"] in TERMINAL:
            response.update(execution_status="stopping", pending_execution_status=state["execution_status"])
        return response


def cancel(task, project):
    with locked(task):
        folder = owned(task, project)
        state = reconcile(folder)
        if state["execution_status"] not in TERMINAL:
            atomic(folder / "cancel.json", {"requested_at": now()})
    for _ in range(100):
        state = status(task, project)
        if not state["process_alive"]:
            return state
        time.sleep(0.05)
    raise ValueError("cancellation not confirmed; query status again")


def collect(task, project):
    with locked(task):
        folder = owned(task, project)
        state = reconcile(folder)
        if state["execution_status"] not in TERMINAL or alive(state):
            raise ValueError("task not stopped")
        result = read(folder / "result.json")
        destination = confined(EXCHANGE, task)
        staging = Path(tempfile.mkdtemp(prefix=".collect-", dir=destination))
        try:
            for artifact in result["artifacts"]:
                source = confined(folder, artifact["path"])
                if file_hash(source) != artifact["sha256"]:
                    raise ValueError("artifact hash mismatch")
                target = staging / artifact["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            atomic(staging / "result.json", result)
            target = confined(destination, "result")
            if target.exists():
                if file_hash(target / "result.json") != file_hash(staging / "result.json"):
                    raise ValueError("existing collection conflict")
                for artifact in result["artifacts"]:
                    if file_hash(confined(target, artifact["path"])) != artifact["sha256"]:
                        raise ValueError("existing artifact corrupted")
            else:
                os.rename(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return {"task_id": task, "project_id": project, "package_hash": state["package_hash"], "result_sha256": file_hash(folder / "result.json")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["probe", "submit", "status", "cancel", "collect", "_supervise"])
    parser.add_argument("task", nargs="?")
    parser.add_argument("project", nargs="?")
    parser.add_argument("lock_fd", nargs="?", type=int)
    args = parser.parse_args()
    try:
        if args.operation == "probe":
            result = {"schema_version": 1, "engines": ["mock", "scientist02"], "source_snapshot_hash": snapshot_hash(), "runtime": str(ROOT), "model_requests": 0, "scientist02_requires": ["approved schema 2 package", "pinned image", "budget gateway"]}
        else:
            identifier(args.task)
            identifier(args.project)
            if args.operation == "_supervise":
                supervise(args.task, args.project, args.lock_fd)
                return
            existing = RUNS / args.task / "research_package.json"
            source = existing if existing.exists() else confined(EXCHANGE, args.task) / "research_package.json"
            if source.exists() and read(source).get("schema_version") == 2:
                import container_tasks
                result = getattr(container_tasks, args.operation)(args.task, args.project)
            else:
                result = globals()[args.operation](args.task, args.project)
        print(canonical(result).decode())
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(2)


if __name__ == "__main__":
    main()
