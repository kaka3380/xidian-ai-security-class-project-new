"""Execute only an explicitly approved review package through the real task chain."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
import time

import httpx
import uvicorn

from executor.protocol import canonical, digest
from workbench.backend.api.app import create_app
from workbench.backend.services.experiment_budget import ExperimentBudgets
from workbench.backend.services.injection_experiment import source_hash, validate_inputs
from workbench.backend.services.research_package import plan_hash
from workbench.backend.store import Store, TERMINAL
from workbench.verify_experiment import verify
from workbench.worker import Worker, worker_lock


def run(review, output, data, key_file, *, apply_confirmed_approval=False):
    if not apply_confirmed_approval:
        raise ValueError("explicit approval of the exact review package and new budget is required")
    review, output = Path(review).resolve(), Path(output).resolve()
    identity = json.loads((review / "review-identity.json").read_bytes())
    plan = json.loads((review / "proposed-plan.json").read_bytes())
    dataset = json.loads((review / "dataset.json").read_bytes())
    catalog = json.loads((review / "sample-catalog.json").read_bytes())
    if plan_hash(plan) != identity["plan_hash"] or digest(canonical(dataset)) != identity["dataset_sha256"] or digest(canonical(catalog)) != identity["sample_catalog_sha256"] or source_hash() != identity["source_hash"]:
        raise ValueError("reviewed plan, sample, catalog or source changed")
    if plan["config"]["budget_cny"] != identity["budget_cny"] or plan["config"]["max_requests"] != identity["max_requests"]:
        raise ValueError("reviewed budget changed")
    validate_inputs(plan, dict(dataset=dataset, responses=None))
    store = Store(data)
    if store.outstanding():
        raise ValueError("existing task must be reconciled before starting a new paid experiment")
    output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (output / name).write_bytes(canonical(value))
    save("human-authorization.json", dict(user_confirmation="批准此方案和新预算 1 元，执行 24 次请求", reviewed=identity,
        recorded_at=datetime.now(timezone.utc).isoformat(), scope="exact synthetic prompts to official DeepSeek; report free; no abstract content sent"))
    app = create_app(store.root)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    gateway = None
    gateway_stream = None
    pointer = store.root / "model-session/gateway.json"
    old_pointer = pointer.read_bytes() if pointer.exists() else None
    pointer_written = False
    task = grant = None
    try:
        with worker_lock(store.root):
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            port = server.servers[0].sockets[0].getsockname()[1]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/workbench", headers={"X-Workbench-Action": "1"}, trust_env=False, timeout=15) as client:
                def request(path, body):
                    response = client.post(path, json=body)
                    response.raise_for_status()
                    return response.json()
                base = f"/projects/{identity['project_id']}"
                approved = request(base + f"/plans/{identity['plan_id']}/approve", dict(reviewed_version=plan["version"], reviewed_hash=identity["plan_hash"]))
                save("approved-plan.json", approved)
                grant = request(base + f"/plans/{identity['plan_id']}/budget-approval", dict(reviewed_version=plan["version"], reviewed_hash=identity["plan_hash"]))
                save("budget-approval.json", grant)
                # Never open or settle the historical model-session/budget.sqlite.
                runtime = store.root / "model-sessions" / grant["grant_id"]
                runtime.mkdir(parents=True, exist_ok=True)
                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    gateway_port = reservation.getsockname()[1]
                gateway_stream = (output / "gateway.log").open("wb")
                gateway = subprocess.Popen([sys.executable, "-m", "workbench.backend.services.model_gateway", "--key-file", str(Path(key_file).resolve()),
                    "--runtime", str(runtime), "--port", str(gateway_port), "--workbench-data", str(store.root)],
                    stdout=gateway_stream, stderr=subprocess.STDOUT, cwd=Path(__file__).resolve().parent.parent,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                deadline = time.monotonic() + 10
                while not (runtime / "gateway.json").is_file() and time.monotonic() < deadline:
                    if gateway.poll() is not None:
                        raise RuntimeError("gateway failed to initialize; inspect the sanitized gateway log")
                    time.sleep(0.05)
                settings = json.loads((runtime / "gateway.json").read_bytes())
                pointer.parent.mkdir(parents=True, exist_ok=True)
                pointer.write_bytes(canonical(settings))
                pointer_written = True
                body = dict(task_type="injection_experiment", idempotency_key="live-" + identity["plan_hash"], experiment=dict(
                    plan_id=identity["plan_id"], plan_hash=identity["plan_hash"], experiment_input=dict(dataset=dataset, responses=None), grant_id=grant["grant_id"]))
                task = request(base + "/tasks", body)
                save("submitted-task.json", task)
                assert request(base + "/tasks", body)["task_id"] == task["task_id"]
                worker = Worker(store)
                worker.tick()
                deadline = time.monotonic() + plan["config"]["experiment"]["timeout_seconds"] + 45
                last = None
                while time.monotonic() < deadline:
                    worker.tick()
                    current = store.task(identity["project_id"], task["task_id"])
                    summary = ExperimentBudgets(store.root).summary(grant["grant_id"])
                    progress = (current["execution_status"], summary["model_requests"])
                    if progress != last:
                        print(json.dumps(dict(stage="experiment", task_id=task["task_id"], status=progress[0], requests=progress[1], reserved_upper_bound_cny=summary["reserved_upper_bound_cny"])), flush=True)
                        last = progress
                    if current["execution_status"] in TERMINAL:
                        break
                    time.sleep(2)
                else:
                    request(base + f"/tasks/{task['task_id']}/cancel", {})
                    worker.tick()
                    raise TimeoutError("experiment deadline; cancellation requested, do not automatically replay")
                save("final-task.json", current)
                save("budget-final.json", summary)
                if current["execution_status"] != "completed" or current["validation_status"] != "passed":
                    raise RuntimeError("experiment did not independently pass: " + str(current["last_error"]))
                record = Path(current["result"]["details"]["folder"])
                independent = verify(record, store.root)
                save("independent-recalculation.json", independent)
                save("review-audit.json", [row for row in client.get(base + "/audit").json() if row["kind"] in {"plan_approval", "experiment_budget_approval"}])
                outcome = dict(status="passed", task_id=task["task_id"], grant_id=grant["grant_id"], plan_hash=identity["plan_hash"],
                    record=str(record), model_requests=summary["model_requests"], reserved_upper_bound_cny=summary["reserved_upper_bound_cny"],
                    budget_cny=summary["budget_cny"], metrics=independent["verified"]["metrics"], review_status="pending_review", report_model_requests=0)
                save("acceptance.json", outcome)
                return outcome
    except Exception as error:
        save("acceptance.json", dict(status="failed", task_id=task["task_id"] if task else None, grant_id=grant["grant_id"] if grant else None,
                                     error=type(error).__name__ + ": " + str(error), automatic_retry=False))
        raise
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=5)
        if gateway is not None:
            if gateway.poll() is None:
                gateway.terminate()
                gateway.wait(timeout=10)
        if gateway_stream is not None:
            gateway_stream.close()
        if pointer_written:
            if old_pointer is None:
                pointer.unlink(missing_ok=True)
            else:
                pointer.write_bytes(old_pointer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--data", type=Path, default=Path("workbench/data"))
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--apply-confirmed-approval", action="store_true")
    args = parser.parse_args()
    print(canonical(run(args.review, args.output, args.data, args.key_file, apply_confirmed_approval=args.apply_confirmed_approval)).decode())
