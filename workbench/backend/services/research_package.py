"""Freeze independently reviewed plans and approved evidence into reproducible packages."""
from copy import deepcopy

from executor.protocol import canonical, digest, identifier
from workbench.backend.services.review_workflow import build_approved_manifest, required_text, timestamp

PLAN_FIELDS = ("project_id", "version", "approved_manifest_hash", "question", "hypothesis", "method", "dataset_id", "dataset_version", "dataset_hash", "baseline", "metrics", "seeds", "engine", "config", "source_snapshot_hash", "image")


def plan_hash(plan):
    try:
        content = {key: deepcopy(plan[key]) for key in PLAN_FIELDS}
    except KeyError as error:
        raise ValueError("missing plan field: " + str(error)) from error
    identifier(content["project_id"])
    identifier(content["dataset_id"])
    if content["engine"] not in {"scientist02", "researcher01"}:
        raise ValueError("invalid research engine")
    if type(content["version"]) is not int or content["version"] < 1:
        raise ValueError("invalid plan version")
    for key in ("question", "hypothesis", "method", "baseline"):
        required_text(content[key], key)
    for key in ("approved_manifest_hash", "dataset_hash", "source_snapshot_hash"):
        value = content[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("invalid " + key)
    if not isinstance(content["metrics"], list) or not content["metrics"] or len(set(content["metrics"])) != len(content["metrics"]):
        raise ValueError("invalid metrics")
    for metric in content["metrics"]:
        identifier(metric)
    if not isinstance(content["seeds"], list) or not content["seeds"] or any(type(s) is not int or s < 0 for s in content["seeds"]):
        raise ValueError("invalid seeds")
    if not isinstance(content["config"], dict) or not content["config"]:
        raise ValueError("missing runtime configuration")
    if type(content["dataset_version"]) is not int or content["dataset_version"] < 1:
        raise ValueError("invalid dataset version")
    if not isinstance(content["image"], str) or not (content["image"].startswith("sha256:") or "@sha256:" in content["image"]):
        raise ValueError("image must be pinned by digest")
    if "experiment" in content["config"]:
        from workbench.backend.services.injection_experiment import contract
        contract(content)
    return digest(canonical(content))


def approve_plan(plan, *, reviewed_version, reviewed_hash, reviewer, reviewed_at):
    current = plan_hash(plan)
    if type(reviewed_version) is not int or reviewed_version != plan["version"] or current != reviewed_hash:
        raise ValueError("plan approval is stale")
    required_text(reviewer, "reviewer")
    timestamp(reviewed_at)
    result = deepcopy(plan)
    result["approval"] = {"version": reviewed_version, "plan_hash": current, "reviewer": reviewer, "reviewed_at": reviewed_at}
    return result


def build_research_package(project_id, task_id, attempt, documents, plan):
    identifier(task_id)
    if type(attempt) is not int or attempt < 1:
        raise ValueError("invalid attempt")
    manifest = build_approved_manifest(project_id, documents)
    if plan.get("project_id") != project_id or plan.get("approved_manifest_hash") != manifest["manifest_sha256"]:
        raise ValueError("plan refers to different or stale evidence")
    approval = plan.get("approval")
    if not isinstance(approval, dict):
        raise ValueError("experiment plan requires independent approval")
    try:
        approve_plan(plan, reviewed_version=approval["version"], reviewed_hash=approval["plan_hash"], reviewer=approval["reviewer"], reviewed_at=approval["reviewed_at"])
    except KeyError as error:
        raise ValueError("invalid plan approval") from error
    selected = {d["document_id"]: d for d in documents if d["project_id"] == project_id}
    files = {}
    entries = []
    for entry in manifest["documents"]:
        name = f"inputs/{entry['document_id']}-v{entry['version']}.txt"
        files[name] = selected[entry["document_id"]]["body"].encode("utf-8")
        entries.append(dict(entry, path=name))
    package = {key: deepcopy(plan[key]) for key in PLAN_FIELDS if key not in {"version"}}
    package.update(schema_version=2, task_id=task_id, attempt=attempt, documents=entries, manifest=manifest, plan_version=plan["version"], plan_hash=plan_hash(plan), approval=deepcopy(approval), config_hash=digest(canonical(plan["config"])))
    package["package_hash"] = digest(canonical(package))
    return package, files


def validate_frozen_package(package, files, *, documents, plan):
    expected, expected_files = build_research_package(package["project_id"], package["task_id"], package["attempt"], documents, plan)
    if package != expected or files != expected_files:
        raise ValueError("frozen package changed or its approvals are stale")
