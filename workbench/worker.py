"""One local worker. Reconcile side effects before accepting new work; never resubmit on restart."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from executor.protocol import canonical, digest
from workbench.adapters.deeptutor import DeepTutor, verify_snapshot
from workbench.backend.store import Store, TERMINAL
from workbench.bridges.scientist02.replay import replay, validate_lock

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_LOCK = ROOT / "deploy/linux/m0/evidence-lock.json"


@contextmanager
def worker_lock(root):
    """OS releases this lock after process death. No stale timestamp takeover."""
    path = Path(root) / "worker.lock"
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("another workbench worker owns this data directory") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Worker:
    def __init__(self, store, adapter=None, *, timeout_seconds=600):
        self.store = store
        self.adapter = adapter or DeepTutor()
        self.timeout_seconds = timeout_seconds

    def recover(self):
        # Repair the DB-commit -> artifact-write crash window without rerunning work.
        for project in self.store.projects():
            for task in self.store.tasks(project["project_id"]):
                if task["execution_status"] in TERMINAL and task["result"] is not None:
                    self._materialize_result(task)
        for task in self.store.outstanding():
            if task["task_type"] == "index_snapshot" and task["external"].get("phase") == "submitted":
                continue  # Poll recorded upstream identity; never POST again.
            if task["execution_status"] not in TERMINAL:
                self.finish(task, "interrupted", "pending", {}, error="worker restarted; automatic replay disabled",
                            external_pending=bool(task["external_pending"]))

    def finish(self, task, status, validation, details, **kwargs):
        result = self.store.finish(task, status, validation, details, **kwargs)
        self._materialize_result(result)
        return result

    def _materialize_result(self, task):
        folder = self.store.root / "tasks" / task["project_id"] / task["task_id"] / f"attempt-{task['attempt']}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "task-result.json"
        content = canonical(task["result"])
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError("task result artifact already exists with different bytes")
        else:
            with path.open("xb") as stream:
                stream.write(content)

    def _poll(self, task):
        external = task["external"]
        stopped = False
        try:
            info = self.adapter.info(external["kb_name"])
            # Recovery without submission acknowledgement only observes termination.
            if task["execution_status"] == "interrupted":
                if info is None or info.get("status") in {"ready", "error", "failed"}:
                    self.store.clear_external_pending(task)
                return
            if info is None:
                self.finish(task, "interrupted", "pending", {}, error="recorded 03 knowledge base is missing", external_pending=False)
                return
            if info.get("status") in {"error", "failed"}:
                self.finish(task, "cancelled" if task["execution_status"] == "cancel_requested" else "failed", "failed", {}, error="03 indexing failed; upstream task has stopped")
                return
            if info.get("status") == "ready":
                stopped = True
                if task["execution_status"] == "cancel_requested":
                    self.finish(task, "cancelled", "not_applicable", {"stop_confirmation": "03 task reached terminal ready; no active version switch"})
                    return
                if info.get("needs_reindex") or info.get("read_only") or info.get("rag_provider") != "llamaindex":
                    raise ValueError("03 knowledge-base provider/binding not ready")
                snapshot = self.store.snapshot(task["project_id"], task["input"]["kb_version"], require_current=True)
                hashes = self.adapter.verify_ready(snapshot, external["kb_name"])
                self.finish(task, "completed", "passed", {"kb_version": snapshot["kb_version"],
                            "approved_manifest_hash": snapshot["approved_manifest_hash"], "external_kb_id": external["kb_name"],
                            "input_files": hashes, "validation_scope": "approved file set and byte integrity; QA quality separate",
                            "paid_model_requests": 0, "local_embedding_requests": "not_metered"}, activate=snapshot["kb_version"])
                return
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(task["started_at"])).total_seconds()
            if elapsed > self.timeout_seconds:
                self.finish(task, "interrupted", "pending", {}, error="03 deadline exceeded; upstream stop not confirmed", external_pending=True)
            else:
                self.store.set_external(task, external, True)
        except Exception as error:
            # A read/validation error cannot prove an external process has stopped.
            self.finish(task, "failed" if stopped else "interrupted", "failed" if stopped else "pending", {},
                        error=type(error).__name__ + ": " + str(error), external_pending=not stopped)

    def _index(self, task):
        external = {}
        pending = False
        try:
            snapshot = self.store.snapshot(task["project_id"], task["input"]["kb_version"], require_current=True)
            verify_snapshot(snapshot)
            if self.adapter.local_contract() != task["input"]["embedding_contract"]:
                raise ValueError("approved local embedding contract changed")
            if task["parent_task_id"]:
                parent = self.store.task(task["project_id"], task["parent_task_id"])
                prior = parent["external"]
                if parent["execution_status"] in {"failed", "interrupted"} and not parent["external_pending"] and parent["input_hash"] == task["input_hash"] and prior.get("phase") == "submitted" and prior.get("api_url") == self.adapter.base_url:
                    info = self.adapter.info(prior["kb_name"])
                    if info and info.get("status") == "ready":
                        self.store.set_external(task, dict(prior, reused_from_task_id=parent["task_id"]), True)
                        return  # Revalidate completed owned index; no duplicate embeddings.
            # Random task identity avoids collisions with existing or foreign KBs.
            name = "wb-" + digest(task["project_id"].encode())[:10] + f"-v{snapshot['kb_version']}-" + task["task_id"][:12]
            external = {"phase": "reserved", "kb_name": name, "api_url": self.adapter.base_url}
            self.store.set_external(task, external, False)
            if self.adapter.info(name) is not None:
                raise ValueError("external KB already exists; ownership unknown")
            if self.store.task(task["project_id"], task["task_id"])["execution_status"] == "cancel_requested":
                self.finish(task, "cancelled", "not_applicable", {"stop_confirmation": "cancelled before 03 submission"})
                return
            # Persist intent before the HTTP side effect; a crash here is ambiguous.
            external["phase"] = "create_requested"
            pending = True
            self.store.set_external(task, external, True)
            upstream_task = self.adapter.submit(snapshot, name, task["input"]["embedding_contract"])
            external.update(phase="submitted", upstream_task_id=upstream_task)
            self.store.set_external(task, external, True)
        except Exception as error:
            self.finish(task, "interrupted" if pending else "failed", "pending" if pending else "failed", {},
                        error=type(error).__name__ + ": " + str(error), external_pending=pending)

    def _replay(self, task):
        try:
            lock = json.loads(EVIDENCE_LOCK.read_bytes())
            validate_lock(ROOT, lock)
            if task["input"] != {"evidence_hash": lock["evidence_hash"]}:
                raise ValueError("replay input lock changed")
            folder = self.store.root / "tasks" / task["project_id"] / task["task_id"] / f"attempt-{task['attempt']}" / "replay"
            result = replay(ROOT, lock, folder, project_id=task["project_id"], task_id=task["task_id"], attempt=task["attempt"], parent_task_id=task["parent_task_id"])
            self.finish(task, "completed", "passed", {"replay_result_hash": result["result_hash"], "folder": str(folder),
                        "evidence_kind": "offline_replay", "research_validation_status": "pending", "model_requests": 0})
        except Exception as error:
            self.finish(task, "failed", "failed", {}, error=type(error).__name__ + ": " + str(error))

    def tick(self):
        outstanding = self.store.outstanding()
        if outstanding:
            task = outstanding[0]
            if task["task_type"] == "index_snapshot" and task["external"].get("kb_name"):
                # Do not switch upstream addresses after restart/config changes.
                if task["external"].get("api_url") != self.adapter.base_url:
                    return False
                self._poll(task)
            return True
        task = self.store.claim()
        if task is None:
            return False
        if task["task_type"] == "index_snapshot":
            self._index(task)
        else:
            self._replay(task)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "workbench/data")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = Store(args.data)
    with worker_lock(store.root):
        worker = Worker(store)
        worker.recover()
        while True:
            worker.tick()
            if args.once:
                return
            time.sleep(1)


if __name__ == "__main__":
    main()
