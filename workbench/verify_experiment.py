"""Recompute an experiment record independently of its executor process."""
import argparse
import json
from pathlib import Path

from executor.protocol import canonical, digest
from workbench.backend.services.experiment_result import verify_record
from workbench.backend.services.injection_experiment import verify_output


def verify(folder, data=None):
    folder = Path(folder)
    package, result, _ = verify_record(folder)
    if "experiment" not in package["config"] or result["validation_status"] != "passed":
        raise ValueError("expected a completed prompt-injection record claiming verification")
    inputs = json.loads((folder / "experiment-input.json").read_bytes())
    verified = verify_output(package, inputs, result, folder)
    budget = None
    if package["config"]["experiment"]["mode"] == "regenerate":
        if data is None:
            raise ValueError("regenerated answers require the independent gateway ledger via --data")
        from workbench.backend.store import Store
        from workbench.backend.services.experiment_budget import ExperimentBudgets
        task = Store(data).task(package["project_id"], package["task_id"])
        if task["external"].get("package_hash") != package["package_hash"]:
            raise ValueError("task ledger package differs")
        responses = json.loads((folder / "experiments/responses.json").read_bytes())
        budget = ExperimentBudgets(data).verify_responses(task["input"]["grant_id"], package, responses)
    return dict(task_id=package["task_id"], package_hash=package["package_hash"], result_hash=digest(canonical(result)),
                validation_status="passed", review_status="pending_review", verified=verified, budget=budget,
                scope="saved exact-answer/marker benchmark; provenance checks do not establish scientific validity")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    print(canonical(verify(args.folder, args.data)).decode("utf-8"))
