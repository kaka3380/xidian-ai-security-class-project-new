"""A new task/attempt of the unchanged approved plan; never overwrite an old run."""
import argparse
import json
from pathlib import Path
import shutil

from executor.protocol import canonical
from workbench.backend.services.research_package import build_research_package, validate_frozen_package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("attempt", type=int)
    args = parser.parse_args()
    if args.attempt < 2:
        raise ValueError("retry attempt must be >= 2")
    exchange = Path("workbench/exchange")
    first = exchange / "scientist02-synthetic-20261022-01"
    task = f"scientist02-synthetic-20261022-{args.attempt:02d}"
    target = exchange / task
    document = json.loads((first / "approved-document.json").read_text())
    plan = json.loads((first / "approved-plan.json").read_text())
    package, files = build_research_package(plan["project_id"], task, args.attempt, [document], plan)
    validate_frozen_package(package, files, documents=[document], plan=plan)
    target.mkdir(exist_ok=False)
    for name in ("approved-document.json", "approved-plan.json", "dataset.npz"):
        shutil.copyfile(first / name, target / name)
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (target / "research_package.json").write_bytes(canonical(package))
    print(json.dumps({"task_id": task, "attempt": args.attempt, "package_hash": package["package_hash"]}))


if __name__ == "__main__":
    main()
