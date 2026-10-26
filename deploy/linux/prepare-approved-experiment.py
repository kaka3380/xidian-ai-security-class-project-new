import importlib.util
import json
from pathlib import Path
import shutil
import yaml
from datetime import datetime, timezone

from executor.protocol import canonical, digest
from workbench.backend.services.review_workflow import apply_review, build_approved_manifest
from workbench.backend.services.research_package import approve_plan, plan_hash, build_research_package, validate_frozen_package
from workbench.adapters.scientist02 import prepare_inputs


def main():
    root = Path(__file__).resolve().parents[2]
    task = "scientist02-synthetic-20261022-01"
    folder = root / "workbench/exchange" / task
    folder.mkdir(parents=True, exist_ok=False)
    module_spec = importlib.util.spec_from_file_location("synthetic", Path(__file__).with_name("synthetic_binary.py"))
    synthetic = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(synthetic)
    dataset_hash = synthetic.make_dataset(folder / "dataset.npz")
    reviewed_plan = json.loads((root / "deploy/linux/stage-d-plan.json").read_text(encoding="utf-8"))
    body = json.dumps(reviewed_plan, ensure_ascii=False, indent=2)
    doc = dict(document_id="approved-synthetic-spec", project_id="integration-demo", version=1, artifact_id="synthetic-spec-v1", title="User-approved synthetic integration specification", authors=["Local project user"], source_url="https://example.invalid/local-synthetic-spec", body=body, sha256=digest(body.encode()), content_level="fulltext", truncated=False, evidence_kind="local_experiment_spec_not_external_paper")
    reviewed_at = datetime.now(timezone.utc).isoformat()
    doc = apply_review(doc, decision="approved", reviewed_version=1, reviewed_sha256=doc["sha256"], reviewer="user-session-approval", reviewed_at=reviewed_at)
    manifest = build_approved_manifest(doc["project_id"], [doc])
    alias = "gpt-4o-2024-11-20"
    method = "Load /task/dataset.npz, never modify or regenerate it. Fit majority-class baseline on y_train only. Train PyTorch MLP Linear(8,16),ReLU,Linear(16,1), BCEWithLogitsLoss, Adam(lr=0.03), 40 epochs, torch.manual_seed(42), CPU threads=1. Evaluate ONLY x_test/y_test once. Save metrics.json with accuracy, baseline_accuracy, train_seconds, seed=42, dataset_sha256 and predictions (64 binary test predictions). Save training loss plot. Do not tune on test labels. For ablation compare the fixed baseline; do not change data, architecture, split or seed. Use /task/ideas.py as reference. This is a software integration experiment, not a novelty claim."
    plan = dict(project_id=doc["project_id"], version=1, approved_manifest_hash=manifest["manifest_sha256"], question="Can the integrated agent reproduce a fixed synthetic binary-classification experiment?", hypothesis="The fixed MLP exceeds the training-majority baseline on the held-out split", method=method, dataset_id="synthetic-binary-256x8", dataset_version=1, dataset_hash=dataset_hash, baseline="Training-label majority class", metrics=["accuracy", "baseline_accuracy", "train_seconds", "seed", "dataset_sha256"], seeds=[42], engine="scientist02", source_snapshot_hash=reviewed_plan["source_stage_a"]["source_archive_sha256"], image="sha256:d9df82aa2d6d8c548478bb73e79d7cc3821e1de6469c4dfef1b3f9bea091dedb", config={"models": {role: alias for role in ("code", "feedback", "vision", "plot")}, "model_mapping": {alias: "deepseek-flash"}, "budget_cny": 5, "timeout_seconds": 1800, "dataset_path": "dataset.npz"})
    plan = approve_plan(plan, reviewed_version=1, reviewed_hash=plan_hash(plan), reviewer="user-session-approval", reviewed_at=reviewed_at)
    package, files = build_research_package(doc["project_id"], task, 1, [doc], plan)
    validate_frozen_package(package, files, documents=[doc], plan=plan)
    for name, data in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name, value in (("research_package.json", package), ("approved-document.json", doc), ("approved-plan.json", plan)):
        (folder / name).write_bytes(canonical(value))
    config = yaml.safe_load((root / "projects/project-02-ai-scientist-v2/bfts_config.yaml").read_text())
    metadata = prepare_inputs(package, config, folder)
    shutil.copyfile(Path(__file__).with_name("synthetic_binary.py"), folder / "ideas.py")
    metadata["command"].append("--load_code")
    (folder / "adapter.json").write_bytes(canonical(metadata))
    approval = {"user_response": "批准此方案并继续", "scope_plan_sha256": digest(canonical(reviewed_plan)), "package_hash": package["package_hash"], "task_id": task, "dataset_sha256": dataset_hash, "provider": "DeepSeek", "budget_cny_all_stages": 5, "reviewed_at_note": "Approval date recorded from this chat; exact human response timestamp is unavailable."}
    (root / "deploy/linux/evidence/stage-d-approval.json").write_bytes(canonical(approval))
    print(json.dumps(approval, ensure_ascii=False))


if __name__ == "__main__":
    main()
