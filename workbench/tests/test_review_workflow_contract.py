"""Test-first contracts for the planned review/version/citation workflow.

No reference implementation is embedded here. Missing production code is
explicitly skipped; REQUIRE_RESEARCH_WORKFLOW=1 makes that gap a test failure.
See README.md for the proposed boundary and its implementation status.
"""

from copy import deepcopy
import hashlib
import importlib
import os
from pathlib import Path

import pytest


MODULE = "workbench.backend.services.review_workflow"


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        # Missing transitive dependencies are real errors, never an excuse to skip.
        parts = MODULE.split(".")
        if exc.name not in {".".join(parts[:i]) for i in range(1, len(parts) + 1)}:
            raise
        reason = f"待实现生产模块 {MODULE}；此用例尚未验证业务功能"
        if os.getenv("REQUIRE_RESEARCH_WORKFLOW") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    for name in ("validate_document", "apply_review", "build_approved_manifest", "validate_answer"):
        assert callable(getattr(module, name, None)), f"Missing contract function: {name}"
    return module


@pytest.fixture
def document():
    body = "这是一份人工构造的摘要测试资料。\n\n验证指标应记录数据集版本。"
    return {
        "document_id": "doc-a", "project_id": "project-a", "version": 1,
        "title": "测试来源卡", "authors": ["示例作者"],
        "source_url": "https://example.invalid/paper-a",
        "content_level": "abstract", "truncated": False, "body": body,
        "artifact_id": "artifact-a-v1",
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "review": None,
    }


@pytest.fixture
def approved(document):
    result = deepcopy(document)
    result["review"] = {
        "decision": "approved", "version": result["version"], "sha256": result["sha256"],
        "reviewer": "reviewer-a", "reviewed_at": "2026-10-22T10:00:00+08:00",
    }
    return result


@pytest.fixture
def answer(approved):
    return {
        "text": "验证指标需要记录数据集版本。", "evidence_status": "grounded",
        "citations": [{
            "document_id": approved["document_id"], "version": approved["version"],
            "sha256": approved["sha256"], "quote": "验证指标应记录数据集版本。",
            "locator": {"kind": "paragraph", "value": 2},
        }],
    }


def review(workflow, document, **overrides):
    arguments = {
        "decision": "approved", "reviewed_version": document["version"],
        "reviewed_sha256": document["sha256"], "reviewer": "reviewer-a",
        "reviewed_at": "2026-10-22T10:00:00+08:00",
    }
    arguments.update(overrides)
    return workflow.apply_review(document, **arguments)


def test_valid_abstract_card_preserves_its_content_boundary(workflow, document):
    before = deepcopy(document)
    assert workflow.validate_document(document) is None
    assert document == before and document["content_level"] == "abstract"


@pytest.mark.parametrize("field", ["document_id", "source_url", "title", "authors"])
def test_source_card_requires_traceable_metadata(workflow, document, field):
    document.pop(field)
    with pytest.raises(ValueError):
        workflow.validate_document(document)


@pytest.mark.parametrize("change", ["hash_mismatch", "invalid_level", "missing_truncation_flag"])
def test_source_card_rejects_inconsistent_content_metadata(workflow, document, change):
    if change == "hash_mismatch":
        document["body"] += "新内容"
    elif change == "invalid_level":
        document["content_level"] = "unknown"
    else:
        document.pop("truncated")
    with pytest.raises(ValueError):
        workflow.validate_document(document)


def test_truncated_card_can_be_stored_but_cannot_be_approved(workflow, document):
    document["truncated"] = True
    assert workflow.validate_document(document) is None
    with pytest.raises(ValueError):
        review(workflow, document)


def test_approval_records_exact_version_hash_and_reviewer_without_mutating_input(workflow, document):
    original = deepcopy(document)
    reviewed = review(workflow, document)
    assert document == original
    assert reviewed["review"] == {
        "decision": "approved", "version": 1, "sha256": original["sha256"],
        "reviewer": "reviewer-a", "reviewed_at": "2026-10-22T10:00:00+08:00",
    }
    assert reviewed["body"] == original["body"]


@pytest.mark.parametrize("override", [
    {"reviewed_version": 0}, {"reviewed_sha256": "0" * 64},
    {"reviewer": ""}, {"decision": "allow-everything"},
])
def test_stale_or_invalid_review_is_rejected(workflow, document, override):
    original = deepcopy(document)
    with pytest.raises(ValueError):
        review(workflow, document, **override)
    assert document == original


def test_manifest_contains_only_approved_documents_of_requested_project(workflow, approved):
    pending = deepcopy(approved)
    pending.update(document_id="pending", review=None)
    rejected = deepcopy(approved)
    rejected["document_id"] = "rejected"
    rejected["review"]["decision"] = "rejected"
    other = deepcopy(approved)
    other.update(document_id="other", project_id="project-b")
    manifest = workflow.build_approved_manifest("project-a", [pending, approved, other, rejected])
    assert manifest["schema_version"] == 1 and manifest["project_id"] == "project-a"
    assert manifest["documents"] == [{
        "document_id": "doc-a", "version": 1, "sha256": approved["sha256"],
        "artifact_id": "artifact-a-v1", "content_level": "abstract",
    }]


def test_manifest_cannot_reuse_approval_after_content_changes(workflow, approved):
    approved["version"] = 2
    approved["body"] += "修订后的结论。"
    approved["sha256"] = hashlib.sha256(approved["body"].encode("utf-8")).hexdigest()
    with pytest.raises(ValueError):
        workflow.build_approved_manifest("project-a", [approved])


def test_manifest_cannot_accept_changed_content_even_when_version_is_unchanged(workflow, approved):
    approved["body"] += "未更新版本和哈希的改动。"
    with pytest.raises(ValueError):
        workflow.build_approved_manifest("project-a", [approved])


def test_manifest_is_an_immutable_snapshot_of_its_input(workflow, approved):
    manifest = workflow.build_approved_manifest("project-a", [approved])
    frozen = deepcopy(manifest)
    approved["version"] = 9
    approved["review"]["decision"] = "rejected"
    assert manifest == frozen


def test_manifest_identity_is_stable_across_input_order(workflow, approved):
    second = deepcopy(approved)
    second.update(document_id="doc-b", artifact_id="artifact-b-v1")
    first = workflow.build_approved_manifest("project-a", [approved, second])
    reordered = workflow.build_approved_manifest("project-a", [second, approved])
    assert first == reordered
    assert len(first["manifest_sha256"]) == 64
    assert all(char in "0123456789abcdef" for char in first["manifest_sha256"])


def test_manifest_identity_changes_after_a_new_version_is_approved(workflow, approved):
    old = workflow.build_approved_manifest("project-a", [approved])
    updated = deepcopy(approved)
    updated.update(version=2, artifact_id="artifact-a-v2")
    updated["body"] += "补充新证据。"
    updated["sha256"] = hashlib.sha256(updated["body"].encode("utf-8")).hexdigest()
    updated = review(workflow, updated)
    new = workflow.build_approved_manifest("project-a", [updated])
    assert old["manifest_sha256"] != new["manifest_sha256"]


@pytest.mark.parametrize("case", ["no_approved_documents", "duplicate_document_id"])
def test_manifest_rejects_empty_or_ambiguous_selection(workflow, approved, case):
    documents = [] if case == "no_approved_documents" else [approved, deepcopy(approved)]
    with pytest.raises(ValueError):
        workflow.build_approved_manifest("project-a", documents)


def test_grounded_answer_has_a_verifiable_quote_in_an_approved_version(workflow, approved, answer):
    manifest = workflow.build_approved_manifest("project-a", [approved])
    assert workflow.validate_answer(answer, manifest, [approved]) is None


@pytest.mark.parametrize("change", [
    {"document_id": "unapproved-document"}, {"version": 99}, {"sha256": "0" * 64},
    {"quote": "来源中不存在的实验结论。"},
    {"locator": {"kind": "paragraph", "value": 1}},
    {"locator": {"kind": "page", "value": 20}},
])
def test_answer_rejects_unapproved_stale_or_fabricated_citations(workflow, approved, answer, change):
    manifest = workflow.build_approved_manifest("project-a", [approved])
    answer["citations"][0].update(change)
    with pytest.raises(ValueError):
        workflow.validate_answer(answer, manifest, [approved])


def test_answer_rejects_document_content_changed_since_indexing(workflow, approved, answer):
    manifest = workflow.build_approved_manifest("project-a", [approved])
    approved["body"] = "原引用已被删除，磁盘内容发生变化。"
    with pytest.raises(ValueError):
        workflow.validate_answer(answer, manifest, [approved])


def test_grounded_answer_cannot_omit_citations(workflow, approved, answer):
    manifest = workflow.build_approved_manifest("project-a", [approved])
    answer["citations"] = []
    with pytest.raises(ValueError):
        workflow.validate_answer(answer, manifest, [approved])


def test_missing_evidence_can_be_reported_explicitly(workflow, approved):
    manifest = workflow.build_approved_manifest("project-a", [approved])
    answer = {"text": "当前资料不足以回答该问题。", "evidence_status": "insufficient", "citations": []}
    assert workflow.validate_answer(answer, manifest, [approved]) is None
