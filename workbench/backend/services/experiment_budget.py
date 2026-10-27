"""New, plan-scoped budget grants; retries share the same durable ceiling."""
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from executor.protocol import canonical, digest
from workbench.backend.services.injection_experiment import contract, request_for
from workbench.backend.services.model_gateway import prepare_request


class ExperimentBudgets:
    def __init__(self, root):
        self.path = Path(root) / "experiment-budgets.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY, project TEXT NOT NULL, plan_hash TEXT NOT NULL,
                    limit_micro INTEGER NOT NULL, max_requests INTEGER NOT NULL, operator TEXT NOT NULL, created REAL NOT NULL, token TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings(task TEXT PRIMARY KEY, grant_id TEXT NOT NULL, package_hash TEXT NOT NULL, requests TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, task TEXT NOT NULL, sample TEXT NOT NULL,
                    request_hash TEXT NOT NULL, reserved INTEGER NOT NULL, status TEXT NOT NULL, response TEXT, UNIQUE(task,sample));
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def approve(self, project, plan_hash, config, operator):
        # Called only by the explicit server-owned approval API, never by enqueue.
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT id FROM grants WHERE project=? AND plan_hash=?", (project, plan_hash)).fetchone()
            if old:
                identifier = old[0]
            else:
                db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?,?,?)", (identifier, project, plan_hash,
                           int(config["budget_cny"] * 1e6), config["max_requests"], operator, time.time(), secrets.token_urlsafe(32)))
        return self.summary(identifier)

    def summary(self, grant):
        with self.connect() as db:
            row = db.execute("SELECT id,project,plan_hash,limit_micro,max_requests,operator,created FROM grants WHERE id=?", (grant,)).fetchone()
            if row is None:
                raise ValueError("new budget approval required")
            calls = db.execute("SELECT id,task,sample,request_hash,reserved,status FROM calls WHERE grant_id=? ORDER BY rowid", (grant,)).fetchall()
        return dict(grant_id=row[0], project_id=row[1], plan_hash=row[2], budget_cny=row[3] / 1e6, max_requests=row[4], operator=row[5], approved_at=row[6],
                    reserved_upper_bound_cny=sum(c[4] for c in calls) / 1e6, model_requests=len(calls),
                    requests=[dict(request_id=c[0], task_id=c[1], sample_id=c[2], request_hash=c[3], reserved_micro=c[4], status=c[5]) for c in calls])

    def validate(self, grant, package):
        summary = self.summary(grant)
        if any(summary[k] != package[k] for k in ("project_id", "plan_hash")) or summary["budget_cny"] != package["config"]["budget_cny"] or summary["max_requests"] != package["config"]["max_requests"]:
            raise ValueError("budget does not match approved plan/configuration")
        return summary

    def bind(self, grant, package, dataset):
        self.validate(grant, package)
        contract(package)
        requests = {row["sample_id"]: digest(canonical(request_for(package, row))) for row in dataset}
        with self.connect() as db:
            old = db.execute("SELECT grant_id,package_hash,requests FROM bindings WHERE task=?", (package["task_id"],)).fetchone()
            expected = (grant, package["package_hash"], canonical(requests).decode())
            if old and old != expected:
                raise ValueError("task budget binding changed")
            db.execute("INSERT OR IGNORE INTO bindings VALUES(?,?,?,?)", (package["task_id"], *expected))

    def credential(self, grant):
        with self.connect() as db:
            row = db.execute("SELECT token FROM grants WHERE id=?", (grant,)).fetchone()
        if row is None:
            raise ValueError("unknown budget grant")
        return row[0]

    def reserve(self, grant, task, sample, body, token):
        request, amount = prepare_request(body)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT limit_micro,max_requests,token FROM grants WHERE id=?", (grant,)).fetchone()
            binding = db.execute("SELECT requests FROM bindings WHERE task=? AND grant_id=?", (task, grant)).fetchone()
            if account is None or not secrets.compare_digest(account[2], token) or binding is None:
                raise ValueError("unauthorized task budget")
            checksum = digest(canonical(body))
            if json.loads(binding[0]).get(sample) != checksum:
                raise ValueError("request differs from approved sample/method/model")
            used, count = db.execute("SELECT COALESCE(SUM(reserved),0),COUNT(*) FROM calls WHERE grant_id=?", (grant,)).fetchone()
            if used + amount > account[0] or count >= account[1]:
                raise ValueError("approved experiment budget/request limit reached")
            call_id = uuid.uuid4().hex
            try:
                db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL)", (call_id, grant, task, sample, checksum, amount, "reserved"))
            except sqlite3.IntegrityError as error:
                raise ValueError("sample already requested; no automatic charged replay") from error
        return call_id, request

    def finish(self, call_id, response=None):
        # Keep full upper-bound reservation even on success. Unknown calls never refund.
        with self.connect() as db:
            db.execute("UPDATE calls SET status=?,response=? WHERE id=? AND status='reserved'",
                       ("completed" if response is not None else "unknown_cost", json.dumps(response) if response is not None else None, call_id))

    def verify_responses(self, grant, package, responses):
        self.validate(grant, package)
        with self.connect() as db:
            for row in responses:
                found = db.execute("SELECT id,status,response FROM calls WHERE grant_id=? AND task=? AND sample=?", (grant, package["task_id"], row["sample_id"])).fetchone()
                if found is None or found[0] != row["request_id"]:
                    raise ValueError("response lacks gateway evidence")
                if found[1] == "unknown_cost" and row["status"] == "error" and row["answer"] == "":
                    continue
                if found[1] != "completed":
                    raise ValueError("response lacks completed gateway evidence")
                data = json.loads(found[2])
                if data.get("model") != row["model"] or data["choices"][0]["message"].get("content") != row["answer"] or row["status"] != "ok":
                    raise ValueError("response differs from raw gateway record")
        return self.summary(grant)
