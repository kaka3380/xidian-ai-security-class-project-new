from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from executor.protocol import canonical, digest
from workbench.bridges.scientist02.journal import (
    STAGES, freeze_journal_inputs, journal_evidence, read_journal_inputs, validate_journal,
)
from workbench.bridges.scientist02.replay import replay, validate_lock
from workbench.backend.services.stage_result import validate_stage_result


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / "outputs/02-functional-check-20261024/artifacts/summary-input"


@pytest.fixture
def journal():
    return {"__version": "2", "nodes": [
        {"id": "root", "code": "print('root')", "parent_id": None, "children": [], "is_buggy": False, "is_buggy_plots": False},
        {"id": "child", "code": "print('child')", "parent_id": None, "children": [], "is_buggy": True, "is_buggy_plots": None},
    ], "node2parent": {"child": "root"}}


@pytest.mark.parametrize("defect", ["duplicate", "unknown_parent", "unknown_child", "self", "cycle", "parent_conflict", "children_conflict", "version", "empty", "code", "flag"])
def test_invalid_graph_is_rejected(journal, defect):
    if defect == "duplicate":
        journal["nodes"].append(deepcopy(journal["nodes"][0]))
    elif defect == "unknown_parent":
        journal["node2parent"]["child"] = "missing"
    elif defect == "unknown_child":
        journal["node2parent"]["missing"] = "root"
    elif defect == "self":
        journal["node2parent"]["child"] = "child"
    elif defect == "cycle":
        journal["node2parent"]["root"] = "child"
    elif defect == "parent_conflict":
        journal["nodes"][1]["parent_id"] = "different"
    elif defect == "children_conflict":
        journal["nodes"][0]["children"] = ["missing"]
    elif defect == "version":
        journal["__version"] = "unknown"
    elif defect == "empty":
        journal["nodes"] = []
    elif defect == "code":
        del journal["nodes"][0]["code"]
    else:
        journal["nodes"][0]["is_buggy"] = "false"
    with pytest.raises(ValueError):
        validate_journal(journal)


def test_authoritative_edges_and_failed_candidates_preserved(journal):
    original = deepcopy(journal)
    stage = journal_evidence([(STAGES[0], journal)])[0]
    assert stage["nodes"][1]["parent_id"] == "root"
    assert stage["node_count"] == 2 and stage["upstream_good_count"] == 1
    assert stage["nodes"][1]["is_buggy"] is True
    assert stage["nodes"][0]["code_sha256"] == digest(b"print('root')")
    assert stage["research_validation_status"] == "pending"
    assert original == journal


@pytest.fixture
def inputs(tmp_path, journal):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "bfts_config.yaml").write_bytes(b"{}")
    for stage in STAGES:
        (root / stage).mkdir()
        (root / stage / "journal.json").write_bytes(canonical(journal))
    return root


@pytest.mark.parametrize("defect", ["missing_stage", "extra_stage", "changed_config", "changed_code", "stage_swap"])
def test_frozen_input_detects_changes(inputs, defect):
    manifest = freeze_journal_inputs(inputs)
    if defect == "missing_stage":
        shutil.rmtree(inputs / STAGES[3])
    elif defect == "extra_stage":
        (inputs / "stage_5_unapproved").mkdir()
    elif defect == "changed_config":
        (inputs / "bfts_config.yaml").write_bytes(b'{"model":"changed"}')
    else:
        target = inputs / STAGES[0] / "journal.json"
        value = json.loads(target.read_bytes())
        value["nodes"][0]["code"] += "\n# changed"
        target.write_bytes(canonical(value))
        if defect == "stage_swap":
            other = inputs / STAGES[1] / "journal.json"
            data = other.read_bytes()
            other.write_bytes(target.read_bytes())
            target.write_bytes(data)
    with pytest.raises(ValueError):
        read_journal_inputs(inputs, manifest)


def test_historical_journals_replay_without_model_dependencies():
    manifest = freeze_journal_inputs(HISTORICAL)
    stages = journal_evidence(read_journal_inputs(HISTORICAL, manifest))
    assert len(stages) == 4
    assert [stage["node_count"] for stage in stages] == [3, 3, 3, 3]
    assert all(stage["research_validation_status"] == "pending" for stage in stages)
    # Cross-stage carried nodes are legitimate; do not silently deduplicate them.
    assert stages[0]["nodes"][1]["node_id"] == stages[1]["nodes"][0]["node_id"]


def make_lock(root):
    payload = {"schema_version": 1, "kind": "scientist02.m0-evidence", "journal_root": "inputs",
               "journal_inputs": freeze_journal_inputs(root / "inputs"), "evidence_files": []}
    return dict(payload, evidence_hash=digest(canonical(payload)))


def test_retry_keeps_previous_attempt_and_same_input_hash(inputs, tmp_path):
    lock = make_lock(tmp_path)
    first = replay(tmp_path, lock, tmp_path / "attempt-01", project_id="p", task_id="replay", attempt=1)
    original = (tmp_path / "attempt-01/stage-result.json").read_bytes()
    with pytest.raises(FileExistsError):
        replay(tmp_path, lock, tmp_path / "attempt-01", project_id="p", task_id="replay", attempt=2)
    second = replay(tmp_path, lock, tmp_path / "attempt-02", project_id="p", task_id="retry", attempt=2, parent_task_id="replay")
    assert first["task"]["input_hash"] == second["task"]["input_hash"]
    assert second["task"]["parent_task_id"] == "replay"
    assert first["details"]["model_requests"] == second["details"]["model_requests"] == 0
    assert first["review_status"] == "pending_review"
    assert (tmp_path / "attempt-01/stage-result.json").read_bytes() == original
    validate_stage_result(second, tmp_path / "attempt-02")


def test_output_cannot_touch_historical_inputs(inputs, tmp_path):
    with pytest.raises(ValueError, match="overlaps"):
        replay(tmp_path, make_lock(tmp_path), inputs / "attempt-01", project_id="p", task_id="r", attempt=1)


def test_lock_and_result_tampering_detected(inputs, tmp_path):
    lock = make_lock(tmp_path)
    altered = deepcopy(lock)
    altered["journal_inputs"]["input_hash"] = "0" * 64
    with pytest.raises(ValueError, match="lock hash"):
        validate_lock(tmp_path, altered)
    result = replay(tmp_path, lock, tmp_path / "attempt", project_id="p", task_id="r", attempt=1)
    (tmp_path / "attempt/journal-evidence.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="artifact hash"):
        validate_stage_result(result, tmp_path / "attempt")
