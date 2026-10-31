"""Local SQLite repository. Versions and attempts are append-only; reviews are audited."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from executor.protocol import canonical, digest, identifier
from workbench.backend.services.review_workflow import apply_review, build_approved_manifest, validate_document
from workbench.backend.services.research_package import approve_plan, build_research_package, plan_hash


TERMINAL = {"completed", "failed", "timed_out", "cancelled", "interrupted"}
TASK_TYPES = {"index_snapshot", "journal_replay", "injection_experiment"}


class Conflict(ValueError):
    pass


class Missing(KeyError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return canonical(value).decode("utf-8")


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "workbench.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES(1);
                CREATE TABLE IF NOT EXISTS projects(
                    project_id TEXT PRIMARY KEY, name TEXT NOT NULL, active_kb_version INTEGER, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents(
                    project_id TEXT NOT NULL REFERENCES projects(project_id), document_id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,document_id,version));
                CREATE TABLE IF NOT EXISTS snapshots(
                    project_id TEXT NOT NULL REFERENCES projects(project_id), kb_version INTEGER NOT NULL,
                    manifest_hash TEXT NOT NULL, manifest TEXT NOT NULL, documents TEXT NOT NULL, files TEXT NOT NULL,
                    folder TEXT NOT NULL, status TEXT NOT NULL, external_kb_id TEXT,
                    PRIMARY KEY(project_id,kb_version), UNIQUE(project_id,manifest_hash));
                CREATE TABLE IF NOT EXISTS plans(
                    project_id TEXT NOT NULL REFERENCES projects(project_id), plan_id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id,plan_id,version));
                CREATE TABLE IF NOT EXISTS tasks(
                    task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
                    attempt INTEGER NOT NULL, task_type TEXT NOT NULL, parent_task_id TEXT REFERENCES tasks(task_id),
                    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, input_hash TEXT NOT NULL, input TEXT NOT NULL,
                    execution_status TEXT NOT NULL, validation_status TEXT NOT NULL, review_status TEXT NOT NULL,
                    external TEXT NOT NULL DEFAULT '{}', external_pending INTEGER NOT NULL DEFAULT 0,
                    heartbeat TEXT, last_error TEXT, result TEXT, created_at TEXT NOT NULL, started_at TEXT,
                    UNIQUE(project_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, kind TEXT NOT NULL,
                    object_id TEXT NOT NULL, operator TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            if db.execute("SELECT version FROM schema_version").fetchall() != [(1,)]:
                raise ValueError("unsupported workbench database schema")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.database, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _project(db, project):
        identifier(project)
        if not db.execute("SELECT 1 FROM projects WHERE project_id=?", (project,)).fetchone():
            raise Missing("project not found")

    @staticmethod
    def _audit(db, project, kind, object_id, operator, payload):
        db.execute("INSERT INTO audit(project_id,kind,object_id,operator,recorded_at,payload) VALUES(?,?,?,?,?,?)",
                   (project, kind, object_id, operator, now(), encode(payload)))

    def create_project(self, project, name):
        identifier(project)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("project name required")
        with self.connect(True) as db:
            existing = db.execute("SELECT name FROM projects WHERE project_id=?", (project,)).fetchone()
            if existing and existing[0] != name:
                raise Conflict("project ID already exists")
            db.execute("INSERT OR IGNORE INTO projects VALUES(?,?,NULL,?)", (project, name, now()))
        return {"project_id": project, "name": name}

    def projects(self):
        with self.connect() as db:
            return [dict(zip(("project_id", "name", "active_kb_version"), row)) for row in db.execute("SELECT project_id,name,active_kb_version FROM projects ORDER BY created_at")]

    @staticmethod
    def _documents(db, project):
        return [json.loads(row[0]) for row in db.execute("""SELECT d.payload FROM documents d WHERE d.project_id=?
            AND d.version=(SELECT MAX(v.version) FROM documents v WHERE v.project_id=d.project_id AND v.document_id=d.document_id)
            ORDER BY d.document_id""", (project,))]

    def documents(self, project):
        with self.connect() as db:
            self._project(db, project)
            return self._documents(db, project)

    def put_document(self, project, document):
        value = deepcopy(document)
        if value.get("project_id") != project or "review" in value:
            raise ValueError("project mismatch or client-supplied review")
        validate_document(value)
        with self.connect(True) as db:
            self._project(db, project)
            previous = db.execute("SELECT version,payload FROM documents WHERE project_id=? AND document_id=? ORDER BY version DESC LIMIT 1", (project, value["document_id"])).fetchone()
            if previous and value["version"] == previous[0]:
                old = json.loads(previous[1])
                old.pop("review", None)
                if value == old:
                    return json.loads(previous[1])
                raise Conflict("document version immutable; create a new version")
            if value["version"] != (previous[0] + 1 if previous else 1):
                raise Conflict("document version must increase by one")
            db.execute("INSERT INTO documents VALUES(?,?,?,?)", (project, value["document_id"], value["version"], encode(value)))
        return value

    def review_document(self, project, doc_id, *, operator, decision, reviewed_version, reviewed_sha256):
        identifier(doc_id)
        with self.connect(True) as db:
            self._project(db, project)
            value = next((d for d in self._documents(db, project) if d["document_id"] == doc_id), None)
            if value is None:
                raise Missing("document not found")
            if value["version"] != reviewed_version or value["sha256"] != reviewed_sha256:
                raise Conflict("reviewed document is stale")
            value = apply_review(value, decision=decision, reviewed_version=reviewed_version,
                                 reviewed_sha256=reviewed_sha256, reviewer=operator, reviewed_at=now())
            db.execute("UPDATE documents SET payload=? WHERE project_id=? AND document_id=? AND version=?", (encode(value), project, doc_id, value["version"]))
            self._audit(db, project, "document_review", doc_id, operator, value["review"])
        return value

    def create_snapshot(self, project, document_ids):
        if not isinstance(document_ids, list) or len(document_ids) != len(set(document_ids)) or len(document_ids) < 5:
            raise ValueError("select at least five distinct approved documents")
        for doc_id in document_ids:
            identifier(doc_id)
        with self.connect(True) as db:
            self._project(db, project)
            documents = [d for d in self._documents(db, project) if d["document_id"] in document_ids]
            if len(documents) != len(document_ids):
                raise ValueError("document missing or from another project")
            manifest = build_approved_manifest(project, documents)
            if len(manifest["documents"]) != len(documents):
                raise ValueError("snapshot cannot contain unapproved documents")
            existing = db.execute("SELECT kb_version FROM snapshots WHERE project_id=? AND manifest_hash=?", (project, manifest["manifest_sha256"])).fetchone()
            if existing:
                return self._snapshot(db, project, existing[0])
            version = db.execute("SELECT COALESCE(MAX(kb_version),0)+1 FROM snapshots WHERE project_id=?", (project,)).fetchone()[0]
            # Unique reservation keeps DB rollback/retry from overwriting a prior folder.
            folder = self.root / "snapshots" / project / (str(version) + "-" + uuid.uuid4().hex)
            folder.mkdir(parents=True)
            files = []
            for document in documents:
                name = f"{document['document_id']}-v{document['version']}.md"
                data = document["body"].encode("utf-8")
                (folder / name).write_bytes(data)
                files.append({"path": name, "sha256": digest(data), "document_id": document["document_id"]})
            (folder / "approved-manifest.json").write_bytes(canonical(manifest))
            (folder / "approved-documents.json").write_bytes(canonical(documents))
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,'pending',NULL)", (project, version, manifest["manifest_sha256"], encode(manifest), encode(documents), encode(files), str(folder)))
            return self._snapshot(db, project, version)

    @staticmethod
    def _snapshot(db, project, version):
        row = db.execute("SELECT manifest_hash,manifest,documents,files,folder,status,external_kb_id FROM snapshots WHERE project_id=? AND kb_version=?", (project, version)).fetchone()
        if row is None:
            raise Missing("knowledge-base version not found")
        return dict(project_id=project, kb_version=version, approved_manifest_hash=row[0], manifest=json.loads(row[1]),
                    documents=json.loads(row[2]), files=json.loads(row[3]), folder=row[4], status=row[5], external_kb_id=row[6])

    @classmethod
    def _snapshot_current(cls, db, snapshot):
        latest = {d["document_id"]: d for d in cls._documents(db, snapshot["project_id"])}
        documents = [latest[d["document_id"]] for d in snapshot["documents"] if d["document_id"] in latest]
        try:
            return build_approved_manifest(snapshot["project_id"], documents) == snapshot["manifest"]
        except ValueError:
            return False

    def snapshot(self, project, version, *, require_current=False):
        with self.connect() as db:
            self._project(db, project)
            value = self._snapshot(db, project, version)
            value["is_current"] = self._snapshot_current(db, value)
            if require_current and not value["is_current"]:
                raise Conflict("approved snapshot is stale; review a new document version")
            return value

    def snapshots(self, project):
        with self.connect() as db:
            self._project(db, project)
            versions = [r[0] for r in db.execute("SELECT kb_version FROM snapshots WHERE project_id=? ORDER BY kb_version", (project,))]
            active = db.execute("SELECT active_kb_version FROM projects WHERE project_id=?", (project,)).fetchone()[0]
            return [dict(self._snapshot(db, project, v), is_current=self._snapshot_current(db, self._snapshot(db, project, v)), is_active=v == active) for v in versions]

    def put_plan(self, project, plan_id, value):
        identifier(plan_id)
        if value.get("project_id") != project or "approval" in value:
            raise ValueError("project mismatch or client-supplied approval")
        plan_hash(value)
        with self.connect(True) as db:
            self._project(db, project)
            snapshot = db.execute("SELECT kb_version FROM snapshots WHERE project_id=? AND manifest_hash=?", (project, value["approved_manifest_hash"])).fetchone()
            if not snapshot or not self._snapshot_current(db, self._snapshot(db, project, snapshot[0])):
                raise Conflict("plan requires a current approved snapshot")
            old = db.execute("SELECT version FROM plans WHERE project_id=? AND plan_id=? ORDER BY version DESC LIMIT 1", (project, plan_id)).fetchone()
            if value["version"] != (old[0] + 1 if old else 1):
                raise Conflict("plan version must increase by one")
            db.execute("INSERT INTO plans VALUES(?,?,?,?)", (project, plan_id, value["version"], encode(value)))
        return value

    def plans(self, project):
        with self.connect() as db:
            self._project(db, project)
            return [dict(json.loads(r[1]), plan_id=r[0]) for r in db.execute("SELECT p.plan_id,p.payload FROM plans p WHERE p.project_id=? AND p.version=(SELECT MAX(q.version) FROM plans q WHERE q.project_id=p.project_id AND q.plan_id=p.plan_id) ORDER BY p.plan_id", (project,))]

    def review_plan(self, project, plan_id, *, operator, reviewed_version, reviewed_hash):
        with self.connect(True) as db:
            self._project(db, project)
            row = db.execute("SELECT payload FROM plans WHERE project_id=? AND plan_id=? ORDER BY version DESC LIMIT 1", (project, identifier(plan_id))).fetchone()
            if row is None:
                raise Missing("plan not found")
            value = json.loads(row[0])
            if value["version"] != reviewed_version or plan_hash(value) != reviewed_hash:
                raise Conflict("reviewed plan is stale")
            snapshot = db.execute("SELECT kb_version FROM snapshots WHERE project_id=? AND manifest_hash=?", (project, value["approved_manifest_hash"])).fetchone()
            if not snapshot or not self._snapshot_current(db, self._snapshot(db, project, snapshot[0])):
                raise Conflict("plan evidence is stale")
            value = approve_plan(value, reviewed_version=reviewed_version, reviewed_hash=reviewed_hash, reviewer=operator, reviewed_at=now())
            db.execute("UPDATE plans SET payload=? WHERE project_id=? AND plan_id=? AND version=?", (encode(value), project, plan_id, value["version"]))
            self._audit(db, project, "plan_approval", plan_id, operator, value["approval"])
        return value

    def package_preview(self, project, plan_id):
        value = next((p for p in self.plans(project) if p["plan_id"] == plan_id), None)
        if value is None:
            raise Missing("plan not found")
        with self.connect() as db:
            snapshot = db.execute("SELECT kb_version FROM snapshots WHERE project_id=? AND manifest_hash=?", (project, value["approved_manifest_hash"])).fetchone()
            if not snapshot or not self._snapshot_current(db, self._snapshot(db, project, snapshot[0])):
                raise Conflict("plan evidence is stale")
            documents = self._snapshot(db, project, snapshot[0])["documents"]
            current = {d["document_id"]: d for d in self._documents(db, project)}
            approved_at = value.get("approval", {}).get("reviewed_at", "")
            if approved_at and any(current[d["document_id"]]["review"]["reviewed_at"] > approved_at for d in documents):
                raise Conflict("plan approval predates a renewed evidence review")
            package, _ = build_research_package(project, "preview-only", 1, documents, value)
            if "experiment" in package["config"]:
                return dict(package=package, execution_enabled=True,
                            reason="explicit inputs required; regeneration additionally requires separately approved budget and scoped gateway")
            return dict(package=package, execution_enabled=False, reason="legacy preview only; choose explicit prompt_injection_v1 profile")

    def _experiment_context(self, db, project, inputs, task_id="preview-only", attempt=1):
        from workbench.backend.services.injection_experiment import validate_inputs, source_hash
        from workbench.backend.services.experiment_budget import ExperimentBudgets
        if set(inputs) != {"plan_id", "plan_hash", "experiment_input", "grant_id"}:
            raise ValueError("experiment requires exact plan, inputs and budget binding")
        row = db.execute("SELECT payload FROM plans WHERE project_id=? AND plan_id=? ORDER BY version DESC LIMIT 1", (project, identifier(inputs["plan_id"]))).fetchone()
        if row is None:
            raise Missing("plan not found")
        plan = json.loads(row[0])
        if plan_hash(plan) != inputs["plan_hash"]:
            raise Conflict("approved plan/configuration changed")
        version = db.execute("SELECT kb_version FROM snapshots WHERE project_id=? AND manifest_hash=?", (project, plan["approved_manifest_hash"])).fetchone()
        if version is None:
            raise Conflict("approved snapshot missing")
        snapshot = self._snapshot(db, project, version[0])
        if not self._snapshot_current(db, snapshot):
            raise Conflict("experiment evidence is stale")
        current = {d["document_id"]: d for d in self._documents(db, project)}
        approved_at = plan.get("approval", {}).get("reviewed_at", "")
        if approved_at and any(current[d["document_id"]]["review"]["reviewed_at"] > approved_at for d in snapshot["documents"]):
            raise Conflict("plan approval predates renewed evidence review")
        package, files = build_research_package(project, task_id, attempt, snapshot["documents"], plan)
        value = validate_inputs(package, inputs["experiment_input"])
        if package["source_snapshot_hash"] != source_hash():
            raise Conflict("approved experiment source snapshot changed")
        if value["mode"] == "regenerate":
            ExperimentBudgets(self.root).validate(inputs["grant_id"], package)
        elif inputs["grant_id"] is not None:
            raise ValueError("frozen analysis cannot use a paid budget")
        return package, files, snapshot["documents"], plan

    def experiment_context(self, task):
        with self.connect() as db:
            return self._experiment_context(db, task["project_id"], task["input"], task["task_id"], task["attempt"])

    def approve_experiment_budget(self, project, plan_id, reviewed_hash, operator):
        from workbench.backend.services.experiment_budget import ExperimentBudgets
        from workbench.backend.services.injection_experiment import contract
        preview = self.package_preview(project, plan_id)["package"]
        if preview["plan_hash"] != reviewed_hash or contract(preview)["mode"] != "regenerate":
            raise Conflict("budget review requires exact approved regeneration plan")
        grant = ExperimentBudgets(self.root).approve(project, reviewed_hash, preview["config"], operator)
        with self.connect(True) as db:
            self._audit(db, project, "experiment_budget_approval", grant["grant_id"], operator, grant)
        return grant

    @staticmethod
    def _task(db, project, task_id):
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM tasks WHERE project_id=? AND task_id=?", (project, identifier(task_id))).fetchone()
        db.row_factory = None
        if row is None:
            raise Missing("task not found")
        value = dict(row)
        for key in ("input", "external", "result"):
            value[key] = json.loads(value[key]) if value[key] is not None else None
        return value

    def _enqueue(self, db, project, task_type, inputs, key, attempt=1, parent=None):
        self._project(db, project)
        identifier(key)
        if task_type not in TASK_TYPES:
            raise ValueError("unsupported task; real experiments and paid stages are not enabled in M1")
        identity = dict(task_type=task_type, input=inputs, attempt=attempt, parent_task_id=parent)
        request_hash = digest(canonical(identity))
        existing = db.execute("SELECT task_id,request_hash FROM tasks WHERE project_id=? AND idempotency_key=?", (project, key)).fetchone()
        if existing:
            if existing[1] != request_hash:
                raise Conflict("idempotency key reused for different input")
            return self._task(db, project, existing[0])
        if task_type == "injection_experiment":
            self._experiment_context(db, project, inputs)
            if db.execute("SELECT 1 FROM tasks WHERE project_id=? AND task_type=? AND input_hash=? AND (execution_status IN ('queued','running','cancel_requested') OR external_pending=1)",
                          (project, task_type, digest(canonical(inputs)))).fetchone():
                raise Conflict("same experiment already pending")
        if task_type == "index_snapshot":
            snapshot = self._snapshot(db, project, inputs["kb_version"])
            if not self._snapshot_current(db, snapshot) or inputs["approved_manifest_hash"] != snapshot["approved_manifest_hash"]:
                raise Conflict("index input is stale")
            if snapshot["status"] == "ready" or db.execute("SELECT 1 FROM tasks WHERE project_id=? AND task_type='index_snapshot' AND input_hash=? AND execution_status IN ('queued','running','cancel_requested')", (project, digest(canonical(inputs)))).fetchone():
                raise Conflict("snapshot already indexed or indexing; inspect existing task")
        task_id = uuid.uuid4().hex
        db.execute("""INSERT INTO tasks(task_id,project_id,attempt,task_type,parent_task_id,idempotency_key,request_hash,input_hash,input,
                   execution_status,validation_status,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'queued','pending','pending_review',?)""",
                   (task_id, project, attempt, task_type, parent, key, request_hash, digest(canonical(inputs)), encode(inputs), now()))
        return self._task(db, project, task_id)

    def enqueue(self, project, task_type, inputs, key):
        with self.connect(True) as db:
            return self._enqueue(db, project, task_type, inputs, key)

    def task(self, project, task_id):
        with self.connect() as db:
            self._project(db, project)
            return self._task(db, project, task_id)

    def tasks(self, project):
        with self.connect() as db:
            self._project(db, project)
            ids = [r[0] for r in db.execute("SELECT task_id FROM tasks WHERE project_id=? ORDER BY created_at DESC", (project,))]
            return [self._task(db, project, t) for t in ids]

    def retry(self, project, task_id, key):
        with self.connect(True) as db:
            task = self._task(db, project, task_id)
            if task["execution_status"] not in TERMINAL or task["external_pending"]:
                raise Conflict("task still running or stop not confirmed")
            if task["task_type"] == "injection_experiment" and task["execution_status"] == "completed":
                raise Conflict("completed experiment cannot be retried; submit a separately authorized task")
            return self._enqueue(db, project, task["task_type"], task["input"], key, task["attempt"] + 1, task_id)

    def cancel(self, project, task_id):
        with self.connect(True) as db:
            task = self._task(db, project, task_id)
            status = task["execution_status"]
            if status not in TERMINAL:
                status = "cancelled" if status == "queued" else "cancel_requested"
                db.execute("UPDATE tasks SET execution_status=?,validation_status=? WHERE task_id=?", (status, "not_applicable" if status == "cancelled" else task["validation_status"], task_id))
            return self._task(db, project, task_id)

    def claim(self):
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM tasks WHERE execution_status IN ('running','cancel_requested') OR external_pending=1").fetchone():
                return None
            row = db.execute("SELECT project_id,task_id FROM tasks WHERE execution_status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE tasks SET execution_status='running',started_at=?,heartbeat=? WHERE task_id=?", (now(), now(), row[1]))
            return self._task(db, *row)

    def outstanding(self):
        with self.connect() as db:
            rows = list(db.execute("SELECT project_id,task_id FROM tasks WHERE execution_status IN ('running','cancel_requested') OR external_pending=1"))
            return [self._task(db, *r) for r in rows]

    def set_external(self, task, external, pending):
        with self.connect(True) as db:
            db.execute("UPDATE tasks SET external=?,external_pending=?,heartbeat=? WHERE task_id=?", (encode(external), int(pending), now(), task["task_id"]))
            if task["task_type"] == "index_snapshot" and external.get("phase") == "submitted":
                db.execute("UPDATE snapshots SET status='indexing' WHERE project_id=? AND kb_version=? AND status!='ready'", (task["project_id"], task["input"]["kb_version"]))

    def finish(self, task, status, validation, details, *, error=None, external_pending=False, activate=None):
        if status not in TERMINAL:
            raise ValueError("terminal status required")
        with self.connect(True) as db:
            current = self._task(db, task["project_id"], task["task_id"])
            if current["execution_status"] in TERMINAL and current["result"] is not None:
                return current
            if current["execution_status"] == "cancel_requested" and status == "completed":
                status, validation, activate = "cancelled", "not_applicable", None
            if activate is not None:
                snapshot = self._snapshot(db, task["project_id"], activate)
                if not self._snapshot_current(db, snapshot):
                    status, validation, error = "failed", "failed", "snapshot changed while indexing; not activated"
                else:
                    db.execute("UPDATE snapshots SET status='ready',external_kb_id=? WHERE project_id=? AND kb_version=?", (current["external"]["kb_name"], task["project_id"], activate))
                    db.execute("UPDATE projects SET active_kb_version=? WHERE project_id=?", (activate, task["project_id"]))
            if task["task_type"] == "index_snapshot" and status != "completed":
                db.execute("UPDATE snapshots SET status=? WHERE project_id=? AND kb_version=? AND status!='ready'", (status, task["project_id"], task["input"]["kb_version"]))
            result = dict(protocol="workbench.task-result", schema_version=1, task_id=task["task_id"], project_id=task["project_id"],
                          task_type=task["task_type"], attempt=task["attempt"], parent_task_id=task["parent_task_id"], input_hash=task["input_hash"],
                          execution_status=status, validation_status=validation, review_status="pending_review", details=details, last_error=error)
            result["result_hash"] = digest(canonical(result))
            db.execute("UPDATE tasks SET execution_status=?,validation_status=?,external_pending=?,result=?,last_error=?,heartbeat=? WHERE task_id=?", (status, validation, int(external_pending), encode(result), error, now(), task["task_id"]))
            return self._task(db, task["project_id"], task["task_id"])

    def clear_external_pending(self, task):
        with self.connect(True) as db:
            db.execute("UPDATE tasks SET external_pending=0,heartbeat=? WHERE task_id=?", (now(), task["task_id"]))
