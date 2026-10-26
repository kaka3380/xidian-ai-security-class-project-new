"""Verify received artifacts and register local experiments as pending review."""
import json
import math
from pathlib import Path
import sqlite3

from executor.protocol import canonical, confined, digest, file_hash, local_path
from workbench.backend.services.research_package import validate_frozen_package


def verify_record(folder):
    folder = local_path(folder)
    package = json.loads((folder / "research_package.json").read_text(encoding="utf-8"))
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    document = json.loads((folder / "approved-document.json").read_text(encoding="utf-8"))
    plan = json.loads((folder / "approved-plan.json").read_text(encoding="utf-8"))
    files = {d["path"]: confined(folder, d["path"]).read_bytes() for d in package["documents"]}
    validate_frozen_package(package, files, documents=[document], plan=plan)
    for key in ("task_id", "project_id", "engine", "package_hash"):
        if result[key] != package[key]:
            raise ValueError("result/package identity mismatch")
    for key in ("image", "source_snapshot_hash"):
        if key in result and result[key] != package[key]:
            raise ValueError("result runtime identity mismatch")
    seen = set()
    for artifact in result["artifacts"]:
        if artifact["path"] in seen or file_hash(confined(folder, artifact["path"])) != artifact["sha256"]:
            raise ValueError("artifact hash mismatch or duplicate")
        seen.add(artifact["path"])
    for value in result["metrics"].values():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite metric")
    if result["validation_status"] == "passed":
        if result["execution_status"] != "completed" or result["exit_code"] != 0 or any(m not in result["metrics"] for m in package["metrics"]):
            raise ValueError("invalid passed result")
    return package, result, document


def register_record(folder, database):
    package, result, _ = verify_record(folder)
    record_hash = digest(canonical(result))
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS experiments(project_id TEXT NOT NULL,task_id TEXT NOT NULL,result_hash TEXT NOT NULL,folder TEXT NOT NULL,review_status TEXT NOT NULL,PRIMARY KEY(project_id,task_id))")
        existing = db.execute("SELECT result_hash FROM experiments WHERE project_id=? AND task_id=?", (package["project_id"], package["task_id"])).fetchone()
        if existing and existing[0] != record_hash:
            raise ValueError("registered result changed")
        db.execute("INSERT OR IGNORE INTO experiments VALUES(?,?,?,?,?)", (package["project_id"], package["task_id"], record_hash, str(Path(folder).resolve()), "pending_review"))
    return {"task_id": package["task_id"], "result_hash": record_hash, "review_status": "pending_review"}
