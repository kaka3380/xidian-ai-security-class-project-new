"""Run from repo root on Windows: python -m deploy.linux.verify-offline.

Only synthetic tasks are launched. --restart-wsl additionally restarts Ubuntu.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid

from executor.protocol import canonical, digest
from workbench import offline_client as client


def wait(task, project="offline-demo"):
    for _ in range(100):
        state = client.invoke("status", task, project)
        if state["execution_status"] in {"completed", "failed", "cancelled", "timed_out", "interrupted"} and not state["process_alive"]:
            return state
        time.sleep(0.1)
    raise AssertionError("task did not stop")


def rejected(operation):
    try:
        operation()
    except ValueError as error:
        return str(error)
    raise AssertionError("expected rejection")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-wsl", action="store_true")
    args = parser.parse_args()
    prefix = "stage-b-" + uuid.uuid4().hex[:10]
    evidence = {"stage": "B", "recorded_at": datetime.now(timezone.utc).isoformat(), "probe": client.invoke("probe"), "checks": {}, "tasks": [], "model_requests": 0}
    checks = evidence["checks"]

    def task(suffix, **kwargs):
        name = prefix + "-" + suffix
        client.create_mock_package(name, **kwargs)
        evidence["tasks"].append(name)
        return name

    name = task("normal", duration=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        submits = list(pool.map(lambda _: client.invoke("submit", name, "offline-demo"), range(4)))
    identities = [s["process"] for s in submits]
    assert all(i == identities[0] for i in identities)
    wait(name)
    result = client.collect_verified(name, "offline-demo")
    assert result["execution_status"] == "completed" and result["validation_status"] == "passed"
    assert result["metrics"]["input_bytes"] == (client.EXCHANGE / name / "inputs/evidence.txt").stat().st_size
    assert client.collect_verified(name, "offline-demo") == result
    checks["roundtrip_hashes_metrics"] = result
    checks["concurrent_idempotency"] = identities
    checks["cross_project_rejected"] = rejected(lambda: client.invoke("collect", name, "wrong-project"))
    package_path = client.EXCHANGE / name / "research_package.json"
    original = package_path.read_bytes()
    package = json.loads(original)
    package["attempt"] = 2
    package_path.write_bytes(canonical(package))
    checks["duplicate_conflict"] = rejected(lambda: client.invoke("submit", name, "offline-demo"))
    package_path.write_bytes(original)

    name = task("cancel", duration=60, timeout=90)
    client.invoke("submit", name, "offline-demo")
    state = client.invoke("cancel", name, "offline-demo")
    assert state["execution_status"] == "cancelled" and not state["process_alive"]
    checks["cancel"] = client.collect_verified(name, "offline-demo")
    for suffix, kwargs, expected in [("timeout", {"duration": 30, "timeout": 0.3}, "timed_out"), ("missing", {"mode": "missing_metrics"}, "completed"), ("failure", {"mode": "fail"}, "failed"), ("retry", {"attempt": 2}, "completed")]:
        name = task(suffix, **kwargs)
        client.invoke("submit", name, "offline-demo")
        if suffix == "timeout":
            time.sleep(1)  # Deliberately no status polling during the deadline.
        assert wait(name)["execution_status"] == expected
        collected = client.collect_verified(name, "offline-demo")
        if suffix == "missing":
            assert collected["validation_status"] == "failed"
        checks[suffix] = collected

    name = task("interrupted", duration=60, timeout=90)
    client.invoke("submit", name, "offline-demo")
    state = client.invoke("status", name, "offline-demo")
    assert state["process_alive"]
    if args.restart_wsl:
        subprocess.run(["wsl.exe", "--terminate", "Ubuntu-24.04"], check=True, capture_output=True, timeout=30)
        checks["restart_kind"] = "actual Ubuntu-24.04 termination and restart"
    else:
        code = "import json,os,signal,sys;sys.path.insert(0,'/home/research/research-runtime/executor');from main import identity;p=json.loads(sys.argv[1]);assert identity(p['pid'])==p;os.kill(p['pid'],signal.SIGKILL)"
        subprocess.run(["wsl.exe", "-d", "Ubuntu-24.04", "-u", "research", "--exec", client.LINUX_PYTHON, "-c", code, json.dumps(state["process"])], check=True, capture_output=True, timeout=30)
        checks["restart_kind"] = "supervisor crash and fresh executor process"
    state = wait(name)
    assert state["execution_status"] == "interrupted"
    assert client.invoke("submit", name, "offline-demo")["execution_status"] == "interrupted"
    checks["restart_no_replay"] = client.collect_verified(name, "offline-demo")
    assert all(v["usage"]["model_requests"] == 0 for v in checks.values() if isinstance(v, dict) and "usage" in v)
    evidence["status"] = "passed"
    target = Path(__file__).parent / "evidence/stage-b-acceptance.json"
    target.write_bytes(canonical(evidence) + b"\n")
    print(json.dumps({"status": "passed", "checks": list(checks), "tasks": evidence["tasks"], "evidence": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
