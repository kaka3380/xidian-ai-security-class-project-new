"""Versioned results for independent stages; legacy executor results stay unchanged."""
from copy import deepcopy
import re

from executor.protocol import canonical, confined, digest, file_hash, identifier


TASK_TYPES = {"journal_replay", "summary", "references", "writeup", "compile", "auto_review"}
EXECUTION = {"queued", "running", "completed", "failed", "timed_out", "cancel_requested", "cancelled", "interrupted"}
VALIDATION = {"pending", "passed", "failed", "not_applicable"}
REVIEW = {"pending_review", "approved", "rejected"}


def require_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid sha256")
    return value


def task_identity(*, project_id, task_id, attempt, task_type, input_hash, parent_task_id=None):
    identifier(project_id)
    identifier(task_id)
    if type(attempt) is not int or attempt < 1:
        raise ValueError("invalid attempt")
    if task_type not in TASK_TYPES:
        raise ValueError("unsupported stage task type")
    require_hash(input_hash)
    if parent_task_id is not None:
        identifier(parent_task_id)
        if parent_task_id == task_id:
            raise ValueError("task cannot be its own parent")
    return dict(project_id=project_id, task_id=task_id, attempt=attempt, task_type=task_type,
                input_hash=input_hash, parent_task_id=parent_task_id)


def build_stage_result(task, *, execution_status, validation_status, evidence_kind,
                       artifacts, details, failure_reason=None):
    """Automatic producers always leave human review pending, even on success."""
    result = dict(schema_version=1, protocol="workbench.stage-result", task=deepcopy(task),
                  execution_status=execution_status, validation_status=validation_status,
                  review_status="pending_review", evidence_kind=evidence_kind,
                  artifacts=deepcopy(artifacts), details=deepcopy(details), failure_reason=failure_reason)
    result["result_hash"] = digest(canonical(result))
    validate_stage_result(result)
    return result


def validate_stage_result(result, folder=None, *, expected_task=None):
    if result.get("schema_version") != 1 or result.get("protocol") != "workbench.stage-result":
        raise ValueError("unsupported stage result protocol")
    task = task_identity(**result["task"])
    if expected_task is not None and task != expected_task:
        raise ValueError("stage task identity mismatch")
    if result.get("execution_status") not in EXECUTION or result.get("validation_status") not in VALIDATION or result.get("review_status") not in REVIEW:
        raise ValueError("invalid stage status")
    if result["validation_status"] == "passed" and result["execution_status"] != "completed":
        raise ValueError("only completed execution can pass validation")
    if result.get("evidence_kind") not in {"offline_replay", "functional_test", "research_validation"}:
        raise ValueError("invalid evidence kind")
    if result["review_status"] == "approved" and (result["validation_status"] != "passed" or result["evidence_kind"] != "research_validation"):
        raise ValueError("functional evidence cannot become approved research")
    if result["execution_status"] in {"failed", "timed_out", "interrupted"} and not result.get("failure_reason"):
        raise ValueError("unsuccessful stage needs a failure reason")
    if not isinstance(result.get("details"), dict) or not isinstance(result.get("artifacts"), list):
        raise ValueError("invalid stage evidence")
    seen = set()
    for artifact in result["artifacts"]:
        name = artifact["path"]
        # Validate portable paths even when this is only a wire-format check.
        path = confined(folder if folder is not None else ".", name)
        if name in seen:
            raise ValueError("duplicate stage artifact")
        seen.add(name)
        require_hash(artifact["sha256"])
        if folder is not None and (not path.is_file() or file_hash(path) != artifact["sha256"]):
            raise ValueError("stage artifact hash mismatch")
    payload = {key: value for key, value in result.items() if key != "result_hash"}
    if require_hash(result.get("result_hash")) != digest(canonical(payload)):
        raise ValueError("stage result hash mismatch")
    return result
