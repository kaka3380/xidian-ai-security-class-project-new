"""Prepare the concrete synthetic experiment for review; no approval is fabricated."""
import json
from pathlib import Path
from executor.protocol import canonical, digest

ROOT = Path(__file__).resolve().parents[2]


def main():
    source = json.loads((ROOT / "deploy/linux/evidence/source-02.json").read_text())
    plan = {"status": "pending_human_review", "scope": "One upstream project-02 small synthetic binary classification run, then project-01 minimal sample if environment permits", "dataset": {"samples": 256, "features": 8, "seed": 42, "split": "192 train / 64 held-out test; immutable indices", "target": "label = (x0 + 0.5*x1 > 0), generated with numpy default_rng(42)"}, "baseline": "majority class fitted on training labels only", "model": "MLP 8 -> 16 -> 1, Adam, 40 epochs, fixed seed", "metrics": ["baseline_accuracy", "accuracy", "train_seconds", "seed", "dataset_sha256"], "limits": {"workers": 1, "seeds": 1, "stages": [2, 2, 1, 1], "single_code_timeout_seconds": 300, "total_timeout_seconds": 1800, "cpu": 4, "memory_gib": 4}, "provider": "DeepSeek", "model_name": "deepseek-flash", "compatibility_alias": "gpt-4o-2024-11-20", "budget_cny_total_all_stages": 5, "budget_control": "durable gateway reservations at peak input/output price; failed requests retain reservation", "source_stage_a": source, "outputs": ["actual generated code", "logs", "metrics", "dataset and split hashes", "model usage", "pending-review report"], "limitations": "Synthetic integration evidence only, not a scientific novelty claim. Human review remains separate from compilation and program exit."}
    target = ROOT / "deploy/linux/stage-d-plan.json"
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(target), "sha256": digest(canonical(plan)), "status": plan["status"]}))


if __name__ == "__main__":
    main()
