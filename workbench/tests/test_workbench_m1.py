from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from executor.protocol import canonical, digest
from workbench.adapters.deeptutor import DeepTutor, verify_snapshot
from workbench.backend.api.app import create_app
from workbench.backend.services.research_package import plan_hash
from workbench.backend.services.source_answer import source_answer
from workbench.backend.store import Conflict, Missing, Store
from workbench.worker import EVIDENCE_LOCK, Worker, worker_lock


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "data")
    value.create_project("p", "Offline fixtures only")
    value.create_project("other", "Other project")
    for i in range(6):
        body = f"Structured queries separate instructions and data in fixture number {i}. This is synthetic test evidence only.\n\nNo real research or source approval is represented."
        doc = dict(project_id="p", document_id=f"doc{i}", version=1, title=f"Fixture {i}", authors=["Fixture author"], source_url="https://example.invalid/fixture", body=body, sha256=digest(body.encode()), artifact_id=f"artifact{i}", content_level="abstract", truncated=False)
        value.put_document("p", doc)
        value.review_document("p", f"doc{i}", operator="fixture-reviewer", decision="approved" if i < 5 else "rejected", reviewed_version=1, reviewed_sha256=doc["sha256"])
    foreign = dict(doc, project_id="other", document_id="foreign")
    value.put_document("other", foreign)
    value.review_document("other", "foreign", operator="fixture-reviewer", decision="approved", reviewed_version=1, reviewed_sha256=foreign["sha256"])
    return value


@pytest.fixture
def snapshot(store):
    return store.create_snapshot("p", [f"doc{i}" for i in range(5)])


class Fake03:
    """Explicit offline protocol fixture, never substituted in production configuration."""
    base_url = "http://127.0.0.1:8001"
    contract = {"selection": {"profile_id": "local", "model_id": "fixture"}, "model": "fixture-local", "provider_mode": "local"}

    def __init__(self):
        self.calls = []
        self.states = {}
        self.corrupt = False

    def local_contract(self):
        return deepcopy(self.contract)

    def info(self, name):
        return self.states.get(name)

    def submit(self, snapshot, name, contract):
        verify_snapshot(snapshot)
        self.calls.append((name, deepcopy(snapshot)))
        self.states[name] = {"status": "processing", "rag_provider": "llamaindex"}
        return "fixture-task"

    def ready(self):
        for info in self.states.values():
            info["status"] = "ready"

    def verify_ready(self, snapshot, name):
        verify_snapshot(snapshot)
        if self.corrupt:
            raise ValueError("03 indexed input hash mismatch")
        return {f["path"]: f["sha256"] for f in snapshot["files"]}

    def search(self, snapshot, query, contract):
        return {"sources": [{"source": f["path"], "content": d["body"][:200], "score": 0.9} for f, d in zip(snapshot["files"], snapshot["documents"])]}


def enqueue_index(store, snapshot, fake, key="index"):
    return store.enqueue("p", "index_snapshot", {"kb_version": snapshot["kb_version"], "approved_manifest_hash": snapshot["approved_manifest_hash"], "embedding_contract": fake.local_contract()}, key)


def change_document(store):
    doc = deepcopy(store.documents("p")[0])
    doc.pop("review")
    doc["version"] += 1
    doc["body"] += "\n\nNew version requiring review."
    doc["sha256"] = digest(doc["body"].encode())
    store.put_document("p", doc)
    return doc


def complete_index(store, snapshot, fake):
    task = enqueue_index(store, snapshot, fake)
    worker = Worker(store, fake)
    worker.tick()
    fake.ready()
    worker.tick()
    return store.task("p", task["task_id"])


def test_only_five_approved_documents_reach_snapshot(store, snapshot):
    assert len(snapshot["files"]) == 5
    assert {d["document_id"] for d in snapshot["documents"]} == {f"doc{i}" for i in range(5)}
    verify_snapshot(snapshot)
    for ids in (["doc0", "doc1", "doc2", "doc3", "doc5"], ["doc0", "doc1", "doc2", "doc3", "foreign"]):
        with pytest.raises(ValueError):
            store.create_snapshot("p", ids)
    with pytest.raises(Missing):
        store.snapshot("other", snapshot["kb_version"])


def test_snapshot_and_versions_survive_new_store_instance(store, snapshot):
    reopened = Store(store.root)
    assert reopened.snapshot("p", 1)["manifest"] == snapshot["manifest"]
    original = Path(snapshot["folder"]).joinpath("approved-documents.json").read_bytes()
    change_document(reopened)
    assert reopened.snapshot("p", 1)["is_current"] is False
    with pytest.raises(Conflict, match="stale"):
        reopened.snapshot("p", 1, require_current=True)
    assert Path(snapshot["folder"]).joinpath("approved-documents.json").read_bytes() == original


def test_immutable_document_review_and_audit(store):
    doc = deepcopy(store.documents("p")[0])
    doc.pop("review")
    doc["title"] = "Changed without new version"
    with pytest.raises(Conflict, match="immutable"):
        store.put_document("p", doc)
    with pytest.raises(Conflict, match="stale"):
        store.review_document("p", "doc0", operator="server-user", decision="approved", reviewed_version=2, reviewed_sha256=doc["sha256"])
    doc["review"] = {"reviewer": "client"}
    with pytest.raises(ValueError, match="client-supplied"):
        store.put_document("p", doc)


def test_parallel_idempotency_and_single_claim(store):
    inputs = {"evidence_hash": json.loads(EVIDENCE_LOCK.read_bytes())["evidence_hash"]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = list(pool.map(lambda _: store.enqueue("p", "journal_replay", inputs, "same-key"), range(8)))
        claims = list(pool.map(lambda _: store.claim(), range(4)))
    assert len({t["task_id"] for t in tasks}) == 1
    assert sum(c is not None for c in claims) == 1
    with pytest.raises(Conflict, match="idempotency"):
        store.enqueue("p", "journal_replay", {"evidence_hash": "b" * 64}, "same-key")


def test_queued_cancel_never_executes(store):
    task = store.enqueue("p", "journal_replay", {"evidence_hash": "a" * 64}, "cancel")
    assert store.cancel("p", task["task_id"])["execution_status"] == "cancelled"
    assert store.claim() is None


def test_running_cancel_waits_for_upstream_stop(store, snapshot):
    fake = Fake03()
    task = enqueue_index(store, snapshot, fake)
    worker = Worker(store, fake)
    worker.tick()
    assert store.cancel("p", task["task_id"])["execution_status"] == "cancel_requested"
    worker.tick()
    assert store.task("p", task["task_id"])["execution_status"] == "cancel_requested"
    fake.ready()
    worker.tick()
    assert store.task("p", task["task_id"])["execution_status"] == "cancelled"
    assert store.projects()[0]["active_kb_version"] is None


def test_restart_polls_recorded_task_without_resubmission(store, snapshot):
    fake = Fake03()
    task = enqueue_index(store, snapshot, fake)
    Worker(store, fake).tick()
    reopened = Store(store.root)
    worker = Worker(reopened, fake)
    worker.recover()
    worker.tick()
    assert len(fake.calls) == 1
    assert reopened.task("p", task["task_id"])["execution_status"] == "running"
    fake.ready()
    worker.tick()
    result = reopened.task("p", task["task_id"])
    assert result["execution_status"] == "completed" and result["validation_status"] == "passed"
    assert result["review_status"] == "pending_review"
    assert reopened.projects()[0]["active_kb_version"] == 1
    assert len(fake.calls) == 1


def test_crash_before_external_ack_does_not_create_again(store, snapshot):
    fake = Fake03()
    enqueue_index(store, snapshot, fake)
    task = store.claim()
    external = {"phase": "create_requested", "kb_name": "uncertain", "api_url": fake.base_url}
    store.set_external(task, external, True)
    fake.states["uncertain"] = {"status": "processing"}
    worker = Worker(store, fake)
    worker.recover()
    worker.tick()
    assert store.task("p", task["task_id"])["execution_status"] == "interrupted"
    assert store.claim() is None
    assert not fake.calls
    fake.ready()
    worker.tick()
    assert not store.task("p", task["task_id"])["external_pending"]
    assert store.projects()[0]["active_kb_version"] is None


def test_local_recovery_marks_interrupted_and_retry_has_new_attempt(store):
    lock = json.loads(EVIDENCE_LOCK.read_bytes())
    original = store.enqueue("p", "journal_replay", {"evidence_hash": lock["evidence_hash"]}, "original")
    store.claim()
    worker = Worker(store, Fake03())
    worker.recover()
    assert store.task("p", original["task_id"])["execution_status"] == "interrupted"
    retry = store.retry("p", original["task_id"], "retry")
    assert retry["attempt"] == 2 and retry["parent_task_id"] == original["task_id"]
    assert retry["input_hash"] == original["input_hash"]
    worker.tick()
    assert store.task("p", retry["task_id"])["execution_status"] == "completed"
    old = store.root / "tasks/p" / original["task_id"] / "attempt-1/task-result.json"
    new = store.root / "tasks/p" / retry["task_id"] / "attempt-2/task-result.json"
    assert old.is_file() and new.is_file() and old.read_bytes() != new.read_bytes()


@pytest.mark.parametrize("defect", ["changed_input", "corrupt_upstream", "expired"])
def test_index_cannot_activate_bad_or_unknown_results(store, snapshot, defect):
    fake = Fake03()
    task = enqueue_index(store, snapshot, fake)
    worker = Worker(store, fake, timeout_seconds=-1 if defect == "expired" else 600)
    worker.tick()
    if defect == "changed_input":
        change_document(store)
    if defect == "corrupt_upstream":
        fake.corrupt = True
    if defect != "expired":
        fake.ready()
    worker.tick()
    assert store.task("p", task["task_id"])["execution_status"] == ("interrupted" if defect == "expired" else "failed")
    assert store.projects()[0]["active_kb_version"] is None


def test_snapshot_tampering_fails_before_http_submission(store, snapshot):
    fake = Fake03()
    task = enqueue_index(store, snapshot, fake)
    (Path(snapshot["folder"]) / snapshot["files"][0]["path"]).write_bytes(b"tampered")
    Worker(store, fake).tick()
    assert store.task("p", task["task_id"])["execution_status"] == "failed"
    assert not fake.calls


def test_new_version_only_activates_after_verified_index(store, snapshot):
    fake = Fake03()
    complete_index(store, snapshot, fake)
    old_bytes = (Path(snapshot["folder"]) / "approved-documents.json").read_bytes()
    changed = change_document(store)
    store.review_document("p", changed["document_id"], operator="fixture-reviewer", decision="approved", reviewed_version=2, reviewed_sha256=changed["sha256"])
    second = store.create_snapshot("p", [f"doc{i}" for i in range(5)])
    assert second["kb_version"] == 2
    assert second["approved_manifest_hash"] != snapshot["approved_manifest_hash"]
    task = enqueue_index(store, second, fake, "second-index")
    worker = Worker(store, fake)
    worker.tick()
    assert store.projects()[0]["active_kb_version"] == 1
    fake.ready()
    worker.tick()
    assert store.projects()[0]["active_kb_version"] == 2
    assert store.task("p", task["task_id"])["execution_status"] == "completed"
    assert store.snapshot("p", 1)["is_current"] is False
    assert (Path(snapshot["folder"]) / "approved-documents.json").read_bytes() == old_bytes


def test_os_worker_lock_is_exclusive(store):
    with worker_lock(store.root):
        with pytest.raises(RuntimeError, match="another"):
            with worker_lock(store.root):
                pass


def test_recover_materializes_committed_result_without_execution(store):
    task = store.enqueue("p", "journal_replay", {"evidence_hash": "a" * 64}, "commit-window")
    task = store.claim()
    store.finish(task, "failed", "failed", {}, error="fixture crash after DB commit")
    fake = Fake03()
    Worker(store, fake).recover()
    artifact = store.root / "tasks/p" / task["task_id"] / "attempt-1/task-result.json"
    assert json.loads(artifact.read_bytes())["execution_status"] == "failed"
    assert not fake.calls


def test_http_upload_exact_files_and_verify_raw_bytes(store, snapshot, monkeypatch):
    fake = Fake03()
    seen = []
    files = {f["path"]: (Path(snapshot["folder"]) / f["path"]).read_bytes() for f in snapshot["files"]}
    def handler(request):
        seen.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/knowledge-bases"
            assert b"doc5" not in request.content and b"foreign" not in request.content
            for name, data in files.items():
                assert name.encode() in request.content and data in request.content
            return httpx.Response(200, json={"task_id": "upstream"})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [{"name": n, "type": "file"} for n in files]})
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[-1]])
    adapter = DeepTutor(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(adapter, "local_contract", fake.local_contract)
    assert adapter.submit(snapshot, "owned", fake.contract) == "upstream"
    assert adapter.verify_ready(snapshot, "owned") == {f["path"]: f["sha256"] for f in snapshot["files"]}


def test_http_detail_normalizes_native_metadata():
    adapter = DeepTutor(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "status": "ready", "metadata": {"rag_provider": "llamaindex", "needs_reindex": False},
        "statistics": {"active_match": True}, "read_only": False})))
    info = adapter.info("owned")
    assert info["rag_provider"] == "llamaindex" and info["needs_reindex"] is False


def test_validation_retry_reuses_finished_index_without_reembedding(store, snapshot):
    fake = Fake03()
    original = enqueue_index(store, snapshot, fake)
    worker = Worker(store, fake)
    worker.tick()
    fake.ready()
    fake.corrupt = True
    worker.tick()
    assert store.task("p", original["task_id"])["execution_status"] == "failed"
    retry = store.retry("p", original["task_id"], "validation-retry")
    fake.corrupt = False
    worker.tick()
    worker.tick()
    assert store.task("p", retry["task_id"])["execution_status"] == "completed"
    assert len(fake.calls) == 1


def test_answer_is_extract_and_outside_question_insufficient(snapshot):
    snapshot.update(is_current=True, external_kb_id="exact-version")
    raw = Fake03().search(snapshot, "structured queries", {})
    inside = source_answer("What are structured queries?", raw, snapshot)
    outside = source_answer("How does superconducting refrigeration work?", raw, snapshot)
    assert inside["evidence_status"] == "grounded" and inside["citations"]
    assert inside["citations"][0]["title"] == snapshot["documents"][0]["title"]
    assert outside["evidence_status"] == "insufficient" and not outside["citations"]
    raw["sources"][0]["source"] = "foreign.md"
    with pytest.raises(ValueError, match="unapproved"):
        source_answer("structured queries", raw, snapshot)


@pytest.mark.asyncio
async def test_api_review_identity_origin_and_version_gate(store, tmp_path):
    app = create_app(store.root, adapter=Fake03(), operator="server-operator")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        url = "/api/workbench/projects/p/documents/doc0/review"
        doc = store.documents("p")[0]
        body = {"decision": "approved", "reviewed_version": 1, "reviewed_sha256": doc["sha256"]}
        assert (await client.post(url, json=body)).status_code == 403
        client.headers["X-Workbench-Action"] = "1"
        assert (await client.post(url, json=body, headers={"Origin": "http://evil.invalid"})).status_code == 403
        assert (await client.post(url, json=dict(body, reviewer="forged"))).status_code == 422
        response = await client.post(url, json=body)
        assert response.status_code == 200
        assert response.json()["review"]["reviewer"] == "server-operator"
        assert (await client.post(url, json=dict(body, reviewed_version=9))).status_code == 409
        assert (await client.get("/api/workbench/projects/p/audit")).json()[-1]["operator"] == "server-operator"


@pytest.mark.asyncio
async def test_api_exact_kb_query_and_old_version_not_silently_current(store, snapshot):
    fake = Fake03()
    complete_index(store, snapshot, fake)
    app = create_app(store.root, adapter=fake, operator="server")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"X-Workbench-Action": "1"}) as client:
        url = "/api/workbench/projects/p/questions"
        body = {"query": "What are structured queries?", "kb_version": 1, "approved_manifest_hash": snapshot["approved_manifest_hash"]}
        assert (await client.post(url, json=body)).json()["evidence_status"] == "grounded"
        assert (await client.post(url, json=dict(body, approved_manifest_hash="a" * 64))).status_code == 409
        change_document(store)
        assert (await client.post(url, json=body)).status_code == 409
        historical = (await client.post(url, json=dict(body, allow_historical=True))).json()
        assert historical["is_current"] is False and historical["kb_version"] == 1


def test_plan_requires_separate_review_and_changes_invalidate(store, snapshot):
    plan = dict(project_id="p", version=1, approved_manifest_hash=snapshot["approved_manifest_hash"], question="Fixture?", hypothesis="Fixture", method="Frozen fixtures", dataset_id="fixture", dataset_version=1, dataset_hash="a" * 64, baseline="Fixture", metrics=["accuracy"], seeds=[42], engine="scientist02", config={"mode": "frozen_answers"}, source_snapshot_hash="b" * 64, image="sha256:" + "c" * 64)
    store.put_plan("p", "plan", plan)
    with pytest.raises(ValueError, match="independent"):
        store.package_preview("p", "plan")
    store.review_plan("p", "plan", operator="fixture-plan-reviewer", reviewed_version=1, reviewed_hash=plan_hash(plan))
    assert store.package_preview("p", "plan")["execution_enabled"] is False
    with pytest.raises(Conflict):
        store.put_plan("p", "plan", dict(plan, method="changed"))
    change_document(store)
    with pytest.raises(Conflict, match="stale"):
        store.package_preview("p", "plan")
