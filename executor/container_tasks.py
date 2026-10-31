"""Supervised scientist02 container tasks, sharing the executor's task store."""
import json
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as state_io
from protocol import canonical, confined, digest, file_hash, identifier
from workbench.backend.services.research_package import validate_frozen_package
from workbench.adapters.scientist02 import prepare_inputs

IMAGE = "sha256:d9df82aa2d6d8c548478bb73e79d7cc3821e1de6469c4dfef1b3f9bea091dedb"
REPAIRED_IMAGE = "sha256:d7fd0f11cbf9b7d7de9a398de76ba55b71c5e81ff70c763361731ecf1aa64857"


def docker(*args, env=None, timeout=30):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env=env)


def inspect_owned(package):
    result = docker("container", "inspect", "research-" + package["task_id"])
    if result.returncode:
        if "No such" in result.stderr:
            return None
        raise ValueError("Docker inspect unavailable; task state cannot be confirmed")
    info = json.loads(result.stdout)[0]
    labels = info["Config"].get("Labels", {})
    if any(labels.get("research." + key) != package[key] for key in ("task_id", "project_id", "package_hash")):
        raise ValueError("container ownership mismatch")
    return info


def stop_owned(package):
    info = inspect_owned(package)
    if info and info["State"]["Running"]:
        stopped = docker("stop", "--time", "5", "research-" + package["task_id"])
        if stopped.returncode or inspect_owned(package)["State"]["Running"]:
            raise ValueError("task container stop could not be confirmed")


def validate_source(folder, task, project):
    package = state_io.read(confined(folder, "research_package.json"))
    plan = state_io.read(confined(folder, "approved-plan.json"))
    documents_path = folder / "approved-documents.json"
    documents = state_io.read(documents_path) if documents_path.exists() else [state_io.read(confined(folder, "approved-document.json"))]
    if package["task_id"] != task or package["project_id"] != project or package["engine"] != "scientist02" or package["image"] not in {IMAGE, REPAIRED_IMAGE}:
        raise ValueError("unsupported task identity, engine or image")
    files = {doc["path"]: confined(folder, doc["path"]).read_bytes() for doc in package["documents"]}
    validate_frozen_package(package, files, documents=documents, plan=plan)
    if "experiment" in package["config"]:
        from workbench.backend.services.injection_experiment import validate_inputs, source_hash, source_inventory
        validate_inputs(package, state_io.read(confined(folder, "experiment-input.json")))
        if package["source_snapshot_hash"] != source_hash():
            raise ValueError("experiment source snapshot mismatch")
        inventory = state_io.read(confined(folder, "source-snapshot.json"))
        if inventory != source_inventory() or any(file_hash(confined(folder, "runtime/" + name)) != checksum for name, checksum in inventory.items()):
            raise ValueError("frozen execution source bytes mismatch")
        return package
    if file_hash(confined(folder, "dataset.npz")) != package["dataset_hash"]:
        raise ValueError("dataset hash mismatch")
    if package["config"]["timeout_seconds"] != 1800:
        raise ValueError("first experiment total timeout must be 1800 seconds")
    return package


def finish(folder, state, outcome, reason=None, exit_code=None):
    package = state_io.read(folder / "research_package.json")
    info = inspect_owned(package)
    if info and info["State"]["Running"]:
        raise ValueError("cannot finish while task container is running")
    if info:
        logs = docker("logs", "--tail", "20000", "research-" + package["task_id"])
        (folder / "engine.log").write_text((logs.stdout + logs.stderr)[-4_000_000:], encoding="utf-8")
    if "experiment" in package["config"]:
        return finish_injection(folder, state, package, outcome, reason, exit_code)
    candidates = []
    for path in (folder / "experiments").rglob("metrics.json") if (folder / "experiments").exists() else []:
        if not path.is_symlink() and path.stat().st_size < 100000:
            try:
                value = state_io.read(path)
                if all(k in value for k in package["metrics"]) and "predictions" in value:
                    candidates.append((path, value))
            except (ValueError, TypeError):
                pass
    metrics, validation = {}, "failed"
    if candidates:
        import numpy as np
        dataset = np.load(folder / "dataset.npz")
        for path, value in candidates:
            predictions = np.asarray(value["predictions"])
            accuracy = float(np.mean(predictions == dataset["y_test"])) if predictions.shape == (64,) else None
            baseline = float(np.mean(int(dataset["y_train"].mean() >= 0.5) == dataset["y_test"]))
            if value["dataset_sha256"] == package["dataset_hash"] and value["seed"] == 42 and accuracy is not None and abs(accuracy - value["accuracy"]) < 1e-9 and abs(baseline - value["baseline_accuracy"]) < 1e-9:
                metrics = {k: value[k] for k in package["metrics"]}
                metrics["verified_predictions_path"] = path.relative_to(folder).as_posix()
                validation = "passed" if outcome == "completed" else "failed"
                break
    artifacts = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        if path.is_file() and not path.is_symlink() and (relative.startswith("experiments/") or relative in {"engine.log", "ideas.json", "ideas.py", "bfts_config.yaml", "adapter.json", "dataset.npz"}):
            confined(folder, relative)
            artifacts.append({"path": relative, "sha256": file_hash(path)})
    result = dict(schema_version=2, task_id=package["task_id"], project_id=package["project_id"], attempt=package["attempt"], package_hash=package["package_hash"], engine=package["engine"], execution_status=outcome, validation_status=validation, exit_code=exit_code, failure_reason=reason, started_at=state.get("started_at"), finished_at=state_io.now(), effective_config=state_io.read(folder / "bfts_config.yaml"), source_snapshot_hash=package["source_snapshot_hash"], image=IMAGE, metrics=metrics, artifacts=artifacts, usage={"model_requests": None, "cost": None, "source": "shared Windows model-session/budget.sqlite"}, record_status="pending_review", evidence_kind="local_synthetic_experiment")
    result["image"] = package["image"]
    state_io.atomic(folder / "result.json", result)
    state.update(execution_status=outcome, finished_at=result["finished_at"], failure_reason=reason)
    state_io.atomic(folder / "state.json", state)
    return state


def finish_injection(folder, state, package, outcome, reason, exit_code):
    from workbench.backend.services.injection_experiment import verify_output
    artifacts = []
    for path in sorted(folder.rglob("*")):
        name = path.relative_to(folder).as_posix()
        if path.is_file() and not path.is_symlink() and (name.startswith(("experiments/", "runtime/")) or name in {"engine.log", "adapter.json", "bfts_config.yaml", "experiment-input.json", "source-snapshot.json"}):
            artifacts.append(dict(path=name, sha256=file_hash(confined(folder, name))))
    result = dict(schema_version=2, task_id=package["task_id"], project_id=package["project_id"], attempt=package["attempt"],
        package_hash=package["package_hash"], engine=package["engine"], execution_status=outcome, validation_status="failed", exit_code=exit_code,
        failure_reason=reason, started_at=state.get("started_at"), finished_at=state_io.now(), effective_config=package["config"],
        source_snapshot_hash=package["source_snapshot_hash"], image=package["image"], metrics={}, artifacts=artifacts,
        usage={"source": "task-scoped experiment budget ledger"}, record_status="pending_review", evidence_kind=package["config"]["experiment"]["mode"])
    if outcome == "completed" and exit_code == 0:
        try:
            result["metrics"] = state_io.read(folder / "experiments/metrics.json")["metrics"]
            verify_output(package, state_io.read(folder / "experiment-input.json"), result, folder)
            result["validation_status"] = "passed"
        except (ValueError, KeyError, TypeError, OSError) as error:
            result["failure_reason"] = "independent verification failed: " + str(error)
    state_io.atomic(folder / "result.json", result)
    state.update(execution_status=outcome, finished_at=result["finished_at"], failure_reason=result["failure_reason"])
    state_io.atomic(folder / "state.json", state)
    return state


def reconcile(folder):
    state = state_io.read(folder / "state.json")
    if state["execution_status"] not in state_io.TERMINAL and not state_io.alive(state):
        package = state_io.read(folder / "research_package.json")
        stop_owned(package)
        if (folder / "result.json").exists():
            result = state_io.read(folder / "result.json")
            state.update(execution_status=result["execution_status"], finished_at=result["finished_at"])
            state_io.atomic(folder / "state.json", state)
        else:
            state = finish(folder, state, "interrupted", "supervisor lost; owned container stopped, no automatic replay")
    return state


def submit(task, project):
    source = confined(state_io.EXCHANGE, task)
    package = validate_source(source, task, project)
    with state_io.locked(task) as lock_fd:
        folder = state_io.RUNS / task
        if folder.exists():
            state_io.owned(task, project)
            state = reconcile(folder)
            if state["package_hash"] != package["package_hash"]:
                raise ValueError("task ID conflict")
            return state
        if docker("image", "inspect", package["image"]).returncode:
            raise ValueError("approved scientist02 image unavailable")
        stage = Path(tempfile.mkdtemp(prefix=".accept-", dir=state_io.RUNS))
        try:
            injection = "experiment" in package["config"]
            document_file = "approved-documents.json" if (source / "approved-documents.json").exists() else "approved-document.json"
            names = ["research_package.json", "approved-plan.json", document_file, "experiment-input.json" if injection else "dataset.npz", *[d["path"] for d in package["documents"]]]
            if injection:
                names.extend(["source-snapshot.json", *["runtime/" + name for name in state_io.read(source / "source-snapshot.json")]])
                if package["config"]["experiment"]["mode"] == "regenerate":
                    names.append("gateway-access.json")
            for name in names:
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(confined(source, name), target)
            validate_source(stage, task, project)
            import yaml
            config = {}
            if not injection:
                source02 = Path((state_io.ROOT / "engines/02/source-path.txt").read_text().strip())
                config = yaml.safe_load((source02 / "bfts_config.yaml").read_text())
            metadata = prepare_inputs(package, config, stage)
            if not injection:
                shutil.copyfile(state_io.ROOT / "support/frozen_experiment.py", stage / "ideas.py")
                metadata["command"].append("--load_code")
            state_io.atomic(stage / "adapter.json", metadata)
            state = {"task_id": task, "project_id": project, "package_hash": package["package_hash"], "execution_status": "accepted", "process": None, "accepted_at": state_io.now(), "container_name": "research-" + task}
            state_io.atomic(stage / "state.json", state)
            os.rename(stage, folder)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_supervise", task, project, str(lock_fd)], pass_fds=(lock_fd,), start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=folder)
        return dict(state, process=state_io.identity(child.pid))


def supervise(task, project, lock_fd):
    folder = state_io.owned(task, project)
    state = state_io.read(folder / "state.json")
    state.update(process=state_io.identity(os.getpid()), execution_status="running", started_at=state_io.now())
    state_io.atomic(folder / "state.json", state)
    os.close(lock_fd)
    package = state_io.read(folder / "research_package.json")
    heavy = None
    try:
        heavy = (state_io.ROOT / "heavy-experiment.lock").open("a+")
        fcntl.flock(heavy, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = state_io.read(folder / "adapter.json")
        injection = "experiment" in package["config"]
        if injection:
            env = dict(os.environ)
            if package["config"]["experiment"]["mode"] == "regenerate":
                gateway = state_io.read(folder / "gateway-access.json")
                if type(gateway.get("port")) is not int or not 1 <= gateway["port"] <= 65535:
                    raise ValueError("invalid scoped gateway port")
                env.update(EXPERIMENT_TOKEN=gateway["token"], EXPERIMENT_GRANT=gateway["grant_id"],
                           EXPERIMENT_GATEWAY_URL=f"http://host.docker.internal:{gateway['port']}/v1/experiment/chat/completions")
        else:
            gateway = state_io.read(Path("/mnt/e/work/ai-security-class-project/workbench/data/model-session/gateway.json"))
            env = dict(os.environ, OPENAI_API_KEY=gateway["token"], OPENAI_BASE_URL=f"http://host.docker.internal:{gateway['port']}/v1")
        (folder / "experiments").mkdir(exist_ok=True)
        args = ["create", "--name", "research-" + task, "--gpus", "all", "--user", "1000:1000", "--cpus", "4", "--memory", "4g", "--memory-swap", "4g", "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m", "--restart", "no", "--workdir", "/task", "--env", "OPENAI_API_KEY", "--env", "OPENAI_BASE_URL", "--env", "MPLCONFIGDIR=/tmp/matplotlib", "--env", "NUMBA_CACHE_DIR=/tmp/numba", "--env", "OMP_NUM_THREADS=1", "--env", "HF_HUB_OFFLINE=1", "--mount", f"type=bind,src={folder},dst=/task,readonly", "--mount", f"type=bind,src={folder / 'experiments'},dst=/task/experiments"]
        if injection:
            # Never inherit a host provider credential into a benchmark container.
            for variable in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
                env.pop(variable, None)
            if package["config"]["experiment"]["mode"] == "frozen_analysis":
                args.extend(["--network", "none"])
            else:
                for variable in ("EXPERIMENT_TOKEN", "EXPERIMENT_GRANT", "EXPERIMENT_GATEWAY_URL"):
                    args.extend(["--env", variable])
            args.extend(["--env", "PYTHONPATH=/task/runtime", "--env", "PYTHONDONTWRITEBYTECODE=1"])
        for key, value in metadata["labels"].items():
            args.extend(["--label", key + "=" + value])
        # Approved inputs and runtime configuration are read-only inside the task.
        for name in (() if injection else ("research_package.json", "approved-document.json", "approved-plan.json", "ideas.json", "ideas.py", "bfts_config.yaml", "adapter.json")):
            args.extend(["--mount", f"type=bind,src={folder / name},dst=/task/{name},readonly"])
        args.extend([package["image"], *metadata["command"]])
        created = docker(*args, env=env)
        if created.returncode:
            raise ValueError("container creation failed: " + created.stderr[-1500:])
        state["container_id"] = created.stdout.strip()
        with state_io.locked(task):
            state_io.atomic(folder / "state.json", state)
        if docker("start", state["container_id"]).returncode:
            raise ValueError("container start failed")
        started = time.monotonic()
        while True:
            with state_io.locked(task):
                info = inspect_owned(package)
                if not info:
                    return finish(folder, state, "interrupted", "container disappeared")
                if not info["State"]["Running"]:
                    code = info["State"]["ExitCode"]
                    return finish(folder, state, "completed" if code == 0 else "failed", None if code == 0 else "upstream engine exited nonzero; see engine.log", code)
                if (folder / "cancel.json").exists():
                    stop_owned(package)
                    return finish(folder, state, "cancelled", "cancel requested")
                deadline = package["config"]["experiment"]["timeout_seconds"] if injection else package["config"]["timeout_seconds"]
                if time.monotonic() - started >= deadline:
                    stop_owned(package)
                    return finish(folder, state, "timed_out", "approved supervisor deadline reached")
            time.sleep(0.5)
    except Exception as error:
        with state_io.locked(task):
            stop_owned(package)
            finish(folder, state, "failed", str(error))
    finally:
        if heavy is not None:
            heavy.close()


def status(task, project):
    with state_io.locked(task):
        if not (state_io.RUNS / task).exists():
            package = validate_source(confined(state_io.EXCHANGE, task), task, project)
            if inspect_owned(package) is not None:
                raise ValueError("orphan container found; stop state unknown")
            return dict(task_id=task, project_id=project, package_hash=package["package_hash"], execution_status="not_found", process_alive=False)
        folder = state_io.owned(task, project)
        state = reconcile(folder)
        active = state_io.alive(state)
        response = dict(state, process_alive=active)
        if active and state["execution_status"] in state_io.TERMINAL:
            response["execution_status"] = "stopping"
        return response


def cancel(task, project):
    with state_io.locked(task):
        folder = state_io.owned(task, project)
        state = reconcile(folder)
        if state["execution_status"] not in state_io.TERMINAL:
            state_io.atomic(folder / "cancel.json", {"requested_at": state_io.now()})
    for _ in range(100):
        state = status(task, project)
        if not state["process_alive"]:
            return state
        time.sleep(0.1)
    raise ValueError("cancellation pending confirmation")


def collect(task, project):
    with state_io.locked(task):
        folder = state_io.owned(task, project)
        state = reconcile(folder)
        if state["execution_status"] not in state_io.TERMINAL or state_io.alive(state):
            raise ValueError("task has not stopped")
        result = state_io.read(folder / "result.json")
        destination = confined(state_io.EXCHANGE, task) / "result"
        destination.mkdir(exist_ok=True)
        for artifact in result["artifacts"]:
            source = confined(folder, artifact["path"])
            if file_hash(source) != artifact["sha256"]:
                raise ValueError("artifact changed")
            target = confined(destination, artifact["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        state_io.atomic(destination / "result.json", result)
        return {"task_id": task, "project_id": project, "package_hash": state["package_hash"], "result_sha256": file_hash(folder / "result.json")}


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "_supervise":
        raise SystemExit(2)
    supervise(identifier(sys.argv[2]), identifier(sys.argv[3]), int(sys.argv[4]))
