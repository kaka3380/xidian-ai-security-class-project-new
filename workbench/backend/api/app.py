"""Thin localhost API; reviewer identity and timestamps are owned by this server."""
import getpass
import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from executor.protocol import canonical, confined, digest, identifier
from workbench.adapters.deeptutor import DeepTutor, UpstreamError
from workbench.backend.store import Conflict, Missing, Store, encode, now
from workbench.backend.services.research_package import plan_hash
from workbench.backend.services.source_answer import source_answer
from workbench.worker import EVIDENCE_LOCK, ROOT


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Project(Payload):
    project_id: str
    name: str = Field(min_length=1, max_length=200)


class Review(Payload):
    decision: Literal["approved", "rejected"]
    reviewed_version: int = Field(ge=1)
    reviewed_sha256: str


class Snapshot(Payload):
    document_ids: list[str] = Field(min_length=5, max_length=100)


class Task(Payload):
    task_type: Literal["journal_replay", "index_snapshot", "injection_experiment"]
    idempotency_key: str
    kb_version: int | None = Field(default=None, ge=1)
    approved_manifest_hash: str | None = None
    experiment: dict | None = None


class Retry(Payload):
    idempotency_key: str


class PlanReview(Payload):
    reviewed_version: int = Field(ge=1)
    reviewed_hash: str


class Question(Payload):
    query: str = Field(min_length=2, max_length=1000)
    kb_version: int = Field(ge=1)
    approved_manifest_hash: str
    allow_historical: bool = False


def create_app(data=None, *, adapter=None, operator=None):
    store = Store(data or ROOT / "workbench/data")
    adapter = adapter or DeepTutor()
    operator = operator or "local:" + getpass.getuser()
    app = FastAPI(title="Research Workbench", version="0.1.0")
    app.state.store = store
    app.state.adapter = adapter
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS answers(answer_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                   kb_version INTEGER NOT NULL,query TEXT NOT NULL,answer TEXT NOT NULL,created_at TEXT NOT NULL)""")

    @app.middleware("http")
    async def mutation_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("x-workbench-action") != "1":
                return JSONResponse({"detail": "workbench action header required"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "cross-origin mutation denied"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(UpstreamError)
    async def upstream_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.get("/api/workbench/health")
    def health():
        return {"status": "ready", "operator": operator, "paid_tasks_enabled": False,
                "milestone": "M2-foundation", "worker_mode": "single", "embedding_policy": "local_only",
                "experiment_profiles": ["prompt_injection_v1"], "paid_execution_requires": "explicit approved plan + new scoped budget + matching gateway",
                "answer_mode": "extractive_evidence", "upstream_liveness": "not_probed_by_this_endpoint"}

    @app.get("/api/workbench/projects")
    def projects():
        return store.projects()

    @app.post("/api/workbench/projects")
    def project(value: Project):
        return store.create_project(value.project_id, value.name)

    @app.get("/api/workbench/projects/{project}/documents")
    def documents(project: str):
        return store.documents(project)

    @app.post("/api/workbench/projects/{project}/documents")
    def document(project: str, value: dict):
        if len(canonical(value)) > 1024 * 1024:
            raise ValueError("document too large")
        return store.put_document(project, value)

    @app.post("/api/workbench/projects/{project}/documents/{document_id}/review")
    def review_document(project: str, document_id: str, value: Review):
        return store.review_document(project, document_id, operator=operator, **value.model_dump())

    @app.get("/api/workbench/projects/{project}/snapshots")
    def snapshots(project: str):
        # Filesystem locations and frozen document bodies are not needed in lists.
        return [{k: v for k, v in s.items() if k not in {"folder", "documents"}} for s in store.snapshots(project)]

    @app.post("/api/workbench/projects/{project}/snapshots")
    def snapshot(project: str, value: Snapshot):
        return {k: v for k, v in store.create_snapshot(project, value.document_ids).items() if k not in {"folder", "documents"}}

    @app.get("/api/workbench/projects/{project}/tasks")
    def tasks(project: str):
        return store.tasks(project)

    @app.post("/api/workbench/projects/{project}/tasks")
    def enqueue(project: str, value: Task):
        if value.task_type == "injection_experiment":
            if value.kb_version is not None or value.approved_manifest_hash is not None or value.experiment is None:
                raise ValueError("experiment accepts only explicit frozen experiment input")
            if len(canonical(value.experiment)) > 1_048_576:
                raise ValueError("experiment input too large")
            return store.enqueue(project, value.task_type, value.experiment, value.idempotency_key)
        if value.experiment is not None:
            raise ValueError("experiment input only allowed for experiment tasks")
        if value.task_type == "journal_replay":
            if value.kb_version is not None or value.approved_manifest_hash is not None:
                raise ValueError("replay does not accept KB parameters")
            inputs = {"evidence_hash": json.loads(EVIDENCE_LOCK.read_bytes())["evidence_hash"]}
        else:
            if value.kb_version is None:
                raise ValueError("explicit KB version required")
            snapshot = store.snapshot(project, value.kb_version, require_current=True)
            if value.approved_manifest_hash != snapshot["approved_manifest_hash"]:
                raise Conflict("explicit manifest hash mismatch")
            inputs = {"kb_version": value.kb_version, "approved_manifest_hash": value.approved_manifest_hash,
                      "embedding_contract": adapter.local_contract()}
        return store.enqueue(project, value.task_type, inputs, value.idempotency_key)

    @app.post("/api/workbench/projects/{project}/tasks/{task_id}/cancel")
    def cancel(project: str, task_id: str):
        return store.cancel(project, task_id)

    @app.post("/api/workbench/projects/{project}/tasks/{task_id}/retry")
    def retry(project: str, task_id: str, value: Retry):
        return store.retry(project, task_id, value.idempotency_key)

    @app.get("/api/workbench/projects/{project}/plans")
    def plans(project: str):
        return [dict(p, plan_hash=plan_hash(p)) for p in store.plans(project)]

    @app.post("/api/workbench/projects/{project}/plans/{plan_id}")
    def plan(project: str, plan_id: str, value: dict):
        return store.put_plan(project, plan_id, value)

    @app.post("/api/workbench/projects/{project}/plans/{plan_id}/approve")
    def approve(project: str, plan_id: str, value: PlanReview):
        return store.review_plan(project, plan_id, operator=operator, **value.model_dump())

    @app.get("/api/workbench/projects/{project}/plans/{plan_id}/package-preview")
    def package_preview(project: str, plan_id: str):
        return store.package_preview(project, plan_id)

    @app.post("/api/workbench/projects/{project}/plans/{plan_id}/budget-approval")
    def approve_budget(project: str, plan_id: str, value: PlanReview):
        plan = next((p for p in store.plans(project) if p["plan_id"] == plan_id), None)
        if plan is None or plan["version"] != value.reviewed_version:
            raise Conflict("budget approval plan version mismatch")
        return store.approve_experiment_budget(project, plan_id, value.reviewed_hash, operator)

    @app.get("/api/workbench/projects/{project}/budgets/{grant_id}")
    def experiment_budget(project: str, grant_id: str):
        from workbench.backend.services.experiment_budget import ExperimentBudgets
        summary = ExperimentBudgets(store.root).summary(grant_id)
        if summary["project_id"] != project:
            raise Missing("budget not found")
        return summary

    @app.get("/api/workbench/experiment-contract")
    def experiment_contract():
        from workbench.backend.services.injection_experiment import METRICS, source_hash
        return dict(profile="prompt_injection_v1", source_snapshot_hash=source_hash(), metrics=METRICS,
                    modes=["frozen_analysis", "regenerate"], regeneration_transport_enabled=True)

    @app.post("/api/workbench/projects/{project}/tasks/{task_id}/report")
    def export_report(project: str, task_id: str):
        from workbench.backend.services.injection_report import build_report, render_report, verify_report
        task = store.task(project, task_id)
        if task["task_type"] != "injection_experiment" or task["execution_status"] != "completed" or task["validation_status"] != "passed":
            raise Conflict("report requires a completed independently verified experiment")
        record = Path(task["result"]["details"]["folder"])
        expected = store.root / "experiments" / project / task_id
        if record.resolve() != expected.resolve():
            raise Conflict("result location does not belong to this task")
        manifest, _ = build_report(record, store.root)
        report_id = manifest["report_id"]
        destination = store.root / "reports" / project / task_id / report_id
        render_report(record, destination, data=store.root)
        verify_report(destination, data=store.root)
        prefix = f"/api/workbench/projects/{project}/tasks/{task_id}/reports/{report_id}"
        return dict(report_id=report_id, index_url=prefix + "/index.html", markdown_url=prefix + "/report.md", manifest_url=prefix + "/report-manifest.json", review_status="pending_review")

    @app.get("/api/workbench/projects/{project}/tasks/{task_id}/reports/{report_id}/{name:path}")
    def report_file(project: str, task_id: str, report_id: str, name: str):
        store.task(project, task_id)
        identifier(report_id)
        folder = store.root / "reports" / project / task_id / report_id
        path = confined(folder, name)
        if not path.is_file():
            raise Missing("report file not found")
        # No secret-bearing files are exported; passive report assets stay local.
        return FileResponse(path, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"})

    @app.post("/api/workbench/projects/{project}/questions")
    def question(project: str, value: Question):
        snapshot = store.snapshot(project, value.kb_version, require_current=not value.allow_historical)
        if snapshot["status"] != "ready" or snapshot["approved_manifest_hash"] != value.approved_manifest_hash:
            raise Conflict("exact indexed KB version and manifest required")
        with store.connect() as db:
            row = db.execute("SELECT input FROM tasks WHERE project_id=? AND task_type='index_snapshot' AND execution_status='completed' ORDER BY created_at DESC", (project,)).fetchall()
        inputs = next((json.loads(r[0]) for r in row if json.loads(r[0])["kb_version"] == value.kb_version), None)
        if inputs is None:
            raise Conflict("no verified indexing task for this version")
        answer = source_answer(value.query, adapter.search(snapshot, value.query, inputs["embedding_contract"]), snapshot)
        if not value.allow_historical:
            store.snapshot(project, value.kb_version, require_current=True)
        import uuid
        answer_id = uuid.uuid4().hex
        with store.connect(True) as db:
            db.execute("INSERT INTO answers VALUES(?,?,?,?,?,?)", (answer_id, project, value.kb_version, value.query, encode(answer), now()))
        return dict(answer, answer_id=answer_id)

    @app.get("/api/workbench/projects/{project}/answers")
    def answers(project: str):
        store.documents(project)
        with store.connect() as db:
            return [dict(answer_id=r[0], query=r[1], **json.loads(r[2])) for r in db.execute("SELECT answer_id,query,answer FROM answers WHERE project_id=? ORDER BY created_at DESC", (project,))]

    @app.get("/api/workbench/projects/{project}/audit")
    def audit(project: str):
        store.documents(project)
        with store.connect() as db:
            return [dict(kind=r[0], object_id=r[1], operator=r[2], recorded_at=r[3], payload=json.loads(r[4])) for r in db.execute("SELECT kind,object_id,operator,recorded_at,payload FROM audit WHERE project_id=? ORDER BY id", (project,))]

    web = ROOT / "projects/project-04-Intelligence Gathering/api/webui"
    if (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="workbench-assets")

    @app.get("/")
    def home():
        return RedirectResponse("/workbench")

    @app.get("/workbench")
    def page():
        if not (web / "index.html").is_file():
            raise HTTPException(503, "build the existing 04 frontend first")
        return FileResponse(web / "index.html")

    return app
