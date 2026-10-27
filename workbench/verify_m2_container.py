"""Real API/Worker/WSL/Docker acceptance using approved synthetic frozen fixtures.

Never starts a model gateway, reads provider keys, or regenerates model answers.
Run explicitly after Docker and the pinned image are available.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Thread
import time

import httpx
import uvicorn

from executor.protocol import canonical, digest
from workbench.backend.api.app import create_app
from workbench.backend.store import Store, TERMINAL
from workbench.verify_experiment import verify as independent_verify
from workbench.worker import Worker


def verify(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Fixture approvals are deliberately confined to this synthetic database.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
    from test_injection_experiment import experiment, snapshot, store
    repository = store.__wrapped__(output / "synthetic")
    version = snapshot.__wrapped__(repository)
    inputs = experiment.__wrapped__(repository, version)
    if inputs["grant_id"] is not None or repository.plans("p")[0]["config"]["budget_cny"] != 0:
        raise ValueError("this acceptance only permits zero-budget frozen fixtures")
    app = create_app(repository.root, operator="synthetic-container-acceptance")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    def save(name, value):
        (output / name).write_bytes(canonical(value))
    task = None
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/workbench", headers={"X-Workbench-Action": "1"}, trust_env=False, timeout=15) as client:
            def request(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
            body = dict(task_type="injection_experiment", idempotency_key="real-container-frozen-01", experiment=inputs)
            task = request("POST", "/projects/p/tasks", json=body)
            duplicate = request("POST", "/projects/p/tasks", json=body)
            assert duplicate["task_id"] == task["task_id"]
            save("submitted-task.json", task)
            print(json.dumps(dict(stage="submitted", task_id=task["task_id"])), flush=True)
            worker = Worker(repository)
            worker.tick()  # Real WSL submission and Docker supervisor, no adapter double.
            submitted = repository.task("p", task["task_id"])
            save("after-wsl-submit.json", submitted)
            if submitted["execution_status"] in TERMINAL:
                raise RuntimeError("submission failed: " + str(submitted["last_error"]))
            # Reopen SQLite and recreate the Worker before reconciliation.
            reopened = Store(repository.root)
            worker = Worker(reopened)
            worker.recover()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                worker.tick()
                current = reopened.task("p", task["task_id"])
                if current["execution_status"] in TERMINAL:
                    break
                time.sleep(0.25)
            else:
                request("POST", f"/projects/p/tasks/{task['task_id']}/cancel", json={})
                raise TimeoutError("acceptance deadline exceeded; cancellation requested, inspect stop confirmation before retry")
            save("final-task.json", current)
            if current["execution_status"] != "completed" or current["validation_status"] != "passed":
                raise RuntimeError("real container task did not pass: " + str(current["last_error"]))
            assert current["review_status"] == "pending_review"
            assert request("POST", "/projects/p/tasks", json=body)["task_id"] == task["task_id"]
            assert len(request("GET", "/projects/p/tasks")) == 1
            record = Path(current["result"]["details"]["folder"])
            verified = independent_verify(record)
            save("independent-recalculation.json", verified)
            assert verified["verified"]["metrics"] == dict(attack_success_rate=0.5, clean_task_accuracy=1, valid_response_rate=2 / 3)
            command = ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "research", "--exec", "docker", "container", "inspect", "research-" + task["task_id"]]
            inspected = subprocess.run(command, capture_output=True, timeout=20, check=True)
            info = json.loads(inspected.stdout)[0]
            environment = dict(entry.split("=", 1) for entry in info["Config"]["Env"] if "=" in entry)
            assert info["HostConfig"]["NetworkMode"] == "none"
            assert info["HostConfig"]["ReadonlyRootfs"] is True
            assert "ALL" in info["HostConfig"]["CapDrop"]
            assert info["State"]["Running"] is False and info["State"]["ExitCode"] == 0
            assert not environment.get("OPENAI_API_KEY") and not environment.get("EXPERIMENT_TOKEN")
            package = json.loads((record / "research_package.json").read_bytes())
            assert info["Image"] == package["image"]
            assert info["Config"]["Labels"]["research.package_hash"] == package["package_hash"]
            save("container.json", dict(container_id=info["Id"], image=info["Image"], state=info["State"], network="none",
                 readonly_rootfs=True, cap_drop=info["HostConfig"]["CapDrop"], labels=info["Config"]["Labels"], provider_credential_present=False))
            with sqlite3.connect(repository.root / "experiments.sqlite") as db:
                assert db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
            result = dict(status="passed", scope="real localhost API -> SQLite -> Worker restart -> WSL -> pinned 02 Docker image -> independent verifier -> result ledger; synthetic frozen answers only",
                task_id=task["task_id"], package_hash=package["package_hash"], source_hash=package["source_snapshot_hash"], image=package["image"],
                approved_fixture_documents=5, duplicate_same_task=True, worker_restart_reconciled=True, unique_registered_results=1,
                metrics=verified["verified"]["metrics"], paid_model_requests=0, budget_cny=0, review_status="pending_review",
                container_network="none", record=str(record), result_sha256=digest((record / "result.json").read_bytes()))
            save("acceptance.json", result)
            return result
    except Exception as error:
        save("acceptance.json", dict(status="failed", error=type(error).__name__ + ": " + str(error), task_id=task["task_id"] if task else None,
            paid_model_requests=0, scope="real container handoff; synthetic frozen fixtures"))
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.output), ensure_ascii=False))
