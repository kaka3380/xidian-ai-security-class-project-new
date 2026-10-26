from copy import deepcopy

import pytest

from executor.protocol import canonical, digest
from workbench.backend.services.stage_result import build_stage_result, task_identity, validate_stage_result


@pytest.fixture
def task():
    return task_identity(project_id="p", task_id="s", attempt=1, task_type="summary", input_hash="a" * 64)


def result_for(task, **kwargs):
    return build_stage_result(task, execution_status="completed", validation_status="pending",
                               evidence_kind="functional_test", artifacts=[], details={}, **kwargs)


def test_execution_completion_does_not_grant_validation_or_review(task):
    result = result_for(task)
    assert result["validation_status"] == "pending"
    assert result["review_status"] == "pending_review"
    assert result["task"] == task
    task["attempt"] = 2
    assert result["task"]["attempt"] == 1
    with pytest.raises(ValueError, match="identity"):
        validate_stage_result(result, expected_task=task)


@pytest.mark.parametrize("field,value", [("attempt", True), ("attempt", 0), ("task_type", "unknown"), ("input_hash", "bad"), ("parent_task_id", "s")])
def test_bad_task_identity(task, field, value):
    task[field] = value
    with pytest.raises(ValueError):
        task_identity(**task)


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled", "interrupted"])
def test_noncompleted_stage_cannot_pass(task, status):
    with pytest.raises(ValueError, match="completed"):
        build_stage_result(task, execution_status=status, validation_status="passed", evidence_kind="functional_test", artifacts=[], details={})


def test_failed_stage_needs_reason(task):
    with pytest.raises(ValueError, match="failure reason"):
        build_stage_result(task, execution_status="failed", validation_status="failed", evidence_kind="functional_test", artifacts=[], details={})


def test_review_cannot_promote_functional_evidence(task):
    result = build_stage_result(task, execution_status="completed", validation_status="passed", evidence_kind="offline_replay", artifacts=[], details={})
    result["review_status"] = "approved"
    result["result_hash"] = digest(canonical({k: v for k, v in result.items() if k != "result_hash"}))
    with pytest.raises(ValueError, match="research"):
        validate_stage_result(result)


@pytest.mark.parametrize("artifact", ["../outside", "C:/outside", "/outside", "a\\b"])
def test_unsafe_artifacts_rejected(task, artifact):
    with pytest.raises(ValueError, match="path"):
        build_stage_result(task, execution_status="completed", validation_status="pending", evidence_kind="functional_test", artifacts=[{"path": artifact, "sha256": "b" * 64}], details={})


def test_envelope_hash_detects_changed_status(task):
    result = deepcopy(result_for(task))
    result["validation_status"] = "failed"
    with pytest.raises(ValueError, match="result hash"):
        validate_stage_result(result)
