"""Offline CLI: validate pinned historical evidence into a fresh attempt folder."""
import argparse
import json
from pathlib import Path

from executor.protocol import canonical, confined, digest, file_hash, local_path
from workbench.backend.services.stage_result import build_stage_result, task_identity, validate_stage_result
from workbench.bridges.scientist02.journal import journal_evidence, read_journal_inputs


def validate_lock(root, lock):
    if lock.get("schema_version") != 1 or lock.get("kind") != "scientist02.m0-evidence":
        raise ValueError("unsupported evidence lock")
    payload = {k: v for k, v in lock.items() if k != "evidence_hash"}
    if lock.get("evidence_hash") != digest(canonical(payload)):
        raise ValueError("evidence lock hash mismatch")
    read_journal_inputs(confined(root, lock["journal_root"]), lock["journal_inputs"])
    seen = set()
    for item in lock["evidence_files"]:
        path = confined(root, item["path"])
        if item["path"] in seen or not path.is_file() or file_hash(path) != item["sha256"]:
            raise ValueError("historical evidence hash mismatch or duplicate")
        seen.add(item["path"])
    return lock


def replay(root, lock, output, *, project_id, task_id, attempt, parent_task_id=None):
    root = local_path(root)
    output = local_path(output)
    # Do not create output inside historical evidence or allow a parent of it.
    source = confined(root, lock["journal_root"]).resolve()
    if output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve()):
        raise ValueError("attempt output overlaps historical journal inputs")
    for item in lock["evidence_files"]:
        original = confined(root, item["path"]).resolve()
        if original.is_relative_to(output.resolve()):
            raise ValueError("attempt output overlaps historical evidence")
    validate_lock(root, lock)
    task = task_identity(project_id=project_id, task_id=task_id, attempt=attempt,
                         task_type="journal_replay", input_hash=lock["evidence_hash"],
                         parent_task_id=parent_task_id)
    # mkdir(exist_ok=False) is the reservation: retry must use a new directory.
    output.mkdir(parents=True, exist_ok=False)
    artifacts = []

    def write(relative, data):
        target = confined(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        artifacts.append({"path": relative, "sha256": digest(data)})

    try:
        write("evidence-lock.json", canonical(lock))
        for item in lock["journal_inputs"]["files"]:
            data = confined(source, item["path"]).read_bytes()
            if digest(data) != item["sha256"]:
                raise ValueError("journal input changed during copy")
            write("inputs/" + item["path"], data)
        for item in lock["evidence_files"]:
            data = confined(root, item["path"]).read_bytes()
            if digest(data) != item["sha256"]:
                raise ValueError("historical evidence changed during copy")
            write("historical/" + item["path"], data)
        journals = read_journal_inputs(output / "inputs", lock["journal_inputs"])
        stages = journal_evidence(journals)
        # Keep the original warning text as evidence; never repair metrics here.
        warning_count = 0
        for item in lock["evidence_files"]:
            if item["path"].endswith("engine.log"):
                warning_count += confined(output, "historical/" + item["path"]).read_text(encoding="utf-8", errors="replace").count("Error in seed result aggregation")
        details = {"validation_scope": "journal structure and frozen file integrity only",
                   "research_validation_status": "pending", "model_requests": 0,
                   "general_checkpoint_resume": "unverified", "seed_aggregation_errors": warning_count,
                   "stages": stages,
                   "limitations": ["Cached answers are not new samples.",
                                   "Historical summaries and figures are not independently validated here.",
                                   "The paper is an independent mixed-source functional test; preserve provenance.",
                                   "This replay grants no model budget or research approval."]}
        write("journal-evidence.json", canonical(details))
        result = build_stage_result(task, execution_status="completed", validation_status="passed",
                                    evidence_kind="offline_replay", artifacts=artifacts, details=details)
    except Exception as error:
        result = build_stage_result(task, execution_status="failed", validation_status="failed",
                                    evidence_kind="offline_replay", artifacts=artifacts, details={},
                                    failure_reason=f"{type(error).__name__}: {error}")
        (output / "stage-result.json").write_bytes(canonical(result))
        raise
    validate_stage_result(result, output, expected_task=task)
    (output / "stage-result.json").write_bytes(canonical(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--lock", type=Path, default=Path("deploy/linux/m0/evidence-lock.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-id", default="integration-m0")
    parser.add_argument("--task-id", default="scientist02-offline-replay")
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--parent-task-id")
    args = parser.parse_args()
    lock_path = args.lock if args.lock.is_absolute() else args.root / args.lock
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    result = replay(args.root, lock, args.output, project_id=args.project_id, task_id=args.task_id,
                    attempt=args.attempt, parent_task_id=args.parent_task_id)
    print(json.dumps({"task": result["task"], "execution_status": result["execution_status"],
                      "validation_status": result["validation_status"], "review_status": result["review_status"],
                      "result": str(args.output / "stage-result.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
