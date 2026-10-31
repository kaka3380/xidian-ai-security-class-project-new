from copy import deepcopy
import pytest

from executor.protocol import canonical, digest
from workbench.backend.services.review_workflow import apply_review, build_approved_manifest
from workbench.backend.services.research_package import approve_plan, build_research_package, plan_hash, validate_frozen_package


@pytest.fixture
def reviewed():
    body = "Synthetic evidence for testing the authorization boundary."
    doc = dict(document_id="doc", project_id="project", artifact_id="artifact", version=1, title="Fixture", authors=["Fixture author"], source_url="https://example.invalid/evidence", body=body, sha256=digest(body.encode()), content_level="abstract", truncated=False)
    doc = apply_review(doc, decision="approved", reviewed_version=1, reviewed_sha256=doc["sha256"], reviewer="human-test", reviewed_at="2026-10-22T12:00:00+08:00")
    manifest = build_approved_manifest("project", [doc])
    plan = dict(project_id="project", version=1, approved_manifest_hash=manifest["manifest_sha256"], question="Test?", hypothesis="Test", method="Fixed split", dataset_id="synthetic", dataset_version=1, dataset_hash="a" * 64, baseline="Majority", metrics=["accuracy"], seeds=[0], engine="scientist02", config={"timeout_seconds": 1800}, source_snapshot_hash="b" * 64, image="sha256:" + "c" * 64)
    plan = approve_plan(plan, reviewed_version=1, reviewed_hash=plan_hash(plan), reviewer="plan-reviewer", reviewed_at="2026-10-22T12:01:00+08:00")
    return doc, plan


def test_roundtrip_is_frozen_and_preserves_abstract(reviewed):
    doc, plan = reviewed
    package, files = build_research_package("project", "task", 1, [doc], plan)
    validate_frozen_package(package, files, documents=[doc], plan=plan)
    assert package["documents"][0]["content_level"] == "abstract"
    assert package["approval"]["reviewer"] == "plan-reviewer"
    snapshot = deepcopy(package)
    plan["config"]["timeout_seconds"] = 1
    assert package == snapshot


@pytest.mark.parametrize("field,value", [("question", "Changed"), ("dataset_hash", "d" * 64), ("seeds", [1]), ("config", {"timeout_seconds": 20}), ("image", "sha256:" + "d" * 64), ("source_snapshot_hash", "d" * 64), ("version", 2)])
def test_plan_changes_invalidate_approval(reviewed, field, value):
    doc, plan = reviewed
    plan[field] = value
    with pytest.raises(ValueError):
        build_research_package("project", "task", 1, [doc], plan)


def test_document_approval_is_not_plan_approval(reviewed):
    doc, plan = reviewed
    plan.pop("approval")
    with pytest.raises(ValueError, match="independent"):
        build_research_package("project", "task", 1, [doc], plan)


@pytest.mark.parametrize("change", ["pending", "rejected", "modified", "new_version", "cross_project"])
def test_unapproved_or_stale_evidence_cannot_be_packaged(reviewed, change):
    doc, plan = reviewed
    if change == "pending":
        doc["review"] = None
    elif change == "rejected":
        doc["review"]["decision"] = "rejected"
    elif change == "modified":
        doc["body"] += "changed"
    elif change == "new_version":
        doc["version"] += 1
    else:
        doc["project_id"] = "other"
    with pytest.raises(ValueError):
        build_research_package("project", "task", 1, [doc], plan)


def test_newly_approved_evidence_requires_new_plan_approval(reviewed):
    doc, plan = reviewed
    doc["body"] += "changed"
    doc["version"] = 2
    doc["sha256"] = digest(doc["body"].encode())
    doc = apply_review(doc, decision="approved", reviewed_version=2, reviewed_sha256=doc["sha256"], reviewer="reviewer", reviewed_at="2026-10-22T13:00:00Z")
    with pytest.raises(ValueError, match="stale evidence"):
        build_research_package("project", "task", 1, [doc], plan)


@pytest.mark.parametrize("change", ["bytes", "package", "revoked"])
def test_frozen_package_rechecked_at_execution_boundary(reviewed, change):
    doc, plan = reviewed
    package, files = build_research_package("project", "task", 1, [doc], plan)
    if change == "bytes":
        files[next(iter(files))] += b"changed"
    elif change == "package":
        package["config"]["timeout_seconds"] = 5
    else:
        doc["review"]["decision"] = "rejected"
    with pytest.raises(ValueError):
        validate_frozen_package(package, files, documents=[doc], plan=plan)
