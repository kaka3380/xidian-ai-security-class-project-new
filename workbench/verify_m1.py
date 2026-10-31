"""Live localhost M1 acceptance. Source review flag requires prior human authorization."""
import argparse
import json
from pathlib import Path
import time

import httpx

from executor.protocol import canonical, digest
from workbench.backend.services.review_workflow import validate_answer

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "prompt-injection-m1"


def verify(output, *, apply_confirmed_review=False, source_review=None, retry_task=None):
    output.mkdir(parents=True, exist_ok=False)
    with httpx.Client(base_url="http://127.0.0.1:8090", timeout=110, trust_env=False, headers={"X-Workbench-Action": "1"}) as client:
        def request(method, suffix, **kwargs):
            response = client.request(method, "/api/workbench" + suffix, **kwargs)
            if response.is_error:
                raise RuntimeError(f"Workbench HTTP {response.status_code}: {response.text[:500]}")
            return response.json()

        def save(name, value):
            with (output / name).open("xb") as stream:
                stream.write(canonical(value))

        base = "/projects/" + PROJECT
        health = request("GET", "/health")
        documents = request("GET", base + "/documents")
        if apply_confirmed_review:
            if source_review is None:
                raise ValueError("exact previously displayed source review required")
            expected = json.loads((source_review / "proposed-documents.json").read_bytes())
            if len(expected) != 6 or [(d["document_id"], d["version"], d["sha256"]) for d in documents] != sorted([(d["document_id"], d["version"], d["sha256"]) for d in expected]):
                raise ValueError("source cards changed since the human review")
            for index, doc in enumerate(expected):
                request("POST", base + f"/documents/{doc['document_id']}/review", json={"decision": "approved" if index < 5 else "rejected", "reviewed_version": doc["version"], "reviewed_sha256": doc["sha256"]})
            save("human-authorization.json", {"source": "direct human confirmation in the current Codex chat", "decision": "approve displayed items 1-5 version1 with exact hashes; reject item6", "displayed_review_sha256": digest((source_review / "proposed-documents.json").read_bytes()), "server_operator": health["operator"]})
        documents = request("GET", base + "/documents")
        approved = [d for d in documents if d.get("review", {}).get("decision") == "approved"]
        assert len(documents) == 6 and len(approved) == 5
        save("reviewed-documents.json", documents)
        save("review-audit.json", request("GET", base + "/audit"))
        snapshot = request("POST", base + "/snapshots", json={"document_ids": [d["document_id"] for d in approved]})
        save("snapshot.json", snapshot)
        payload = {"task_type": "index_snapshot", "kb_version": snapshot["kb_version"], "approved_manifest_hash": snapshot["approved_manifest_hash"], "idempotency_key": "m1-initial-index-01"}
        if retry_task:
            retry_payload = {"idempotency_key": "m1-index-retry-" + retry_task}
            task = request("POST", base + f"/tasks/{retry_task}/retry", json=retry_payload)
        else:
            task = request("POST", base + "/tasks", json=payload)
        save("index-submission.json", task)
        print(json.dumps({"stage": "index_submitted", "task_id": task["task_id"]}), flush=True)
        deadline = time.monotonic() + 240
        last_status = None
        while time.monotonic() < deadline:
            tasks = request("GET", base + "/tasks")
            current = next(t for t in tasks if t["task_id"] == task["task_id"])
            if current["execution_status"] != last_status:
                print(json.dumps({"stage": "index", "status": current["execution_status"]}), flush=True)
                last_status = current["execution_status"]
            if current["execution_status"] in {"completed", "failed", "timed_out", "cancelled", "interrupted"}:
                save("index-task.json", current)
                if current["execution_status"] != "completed":
                    raise RuntimeError("index did not complete: " + str(current["last_error"]))
                break
            time.sleep(2)
        else:
            raise TimeoutError("M1 index acceptance deadline exceeded; inspect persisted task, do not resubmit")
        duplicate = request("POST", base + f"/tasks/{retry_task}/retry", json=retry_payload) if retry_task else request("POST", base + "/tasks", json=payload)
        assert duplicate["task_id"] == task["task_id"]
        questions = [("inside", "How do structured queries separate prompts and data?"), ("outside", "What is the superconducting transition temperature of mercury?")]
        answers = {}
        for kind, query in questions:
            answer = request("POST", base + "/questions", json={"query": query, "kb_version": snapshot["kb_version"], "approved_manifest_hash": snapshot["approved_manifest_hash"]})
            validate_answer(answer, snapshot["manifest"], approved)
            save(kind + "-answer.json", answer)
            answers[kind] = answer["evidence_status"]
            print(json.dumps({"stage": kind + "_question", "evidence_status": answer["evidence_status"]}), flush=True)
        assert answers == {"inside": "grounded", "outside": "insufficient"}
        report = {"schema_version": 1, "milestone": "M1", "status": "passed", "scope": "real local embeddings + native03 index/retrieval; extractive evidence only",
                  "project_id": PROJECT, "approved_documents": 5, "rejected_documents": 1, "kb_version": snapshot["kb_version"],
                  "approved_manifest_hash": snapshot["approved_manifest_hash"], "task_id": task["task_id"], "duplicate_submission_same_task": True,
                  "answers": answers, "paid_model_requests": 0, "local_embedding_requests": "not_metered", "human_result_review_status": "pending_review"}
        save("acceptance.json", report)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-review", type=Path)
    parser.add_argument("--apply-confirmed-review", action="store_true")
    parser.add_argument("--retry-task")
    args = parser.parse_args()
    print(json.dumps(verify(args.output, apply_confirmed_review=args.apply_confirmed_review, source_review=args.source_review, retry_task=args.retry_task), ensure_ascii=False))
