"""Task-scoped inputs for the existing AI-Scientist v2 entry point."""
from copy import deepcopy
import json
from pathlib import Path

from executor.protocol import canonical, digest, identifier


def prepare_inputs(package, base_config, output):
    if package.get("engine") != "scientist02" or package.get("schema_version") != 2:
        raise ValueError("expected an approved scientist02 package")
    task = identifier(package["task_id"])
    models = package["config"].get("models", {})
    if any(not models.get(role) for role in ("code", "feedback", "vision", "plot")):
        raise ValueError("code, feedback, vision and plotting models must be explicit")
    budget = package["config"].get("budget_cny")
    if type(budget) not in (int, float) or not 0 < budget <= 5:
        raise ValueError("invalid approved session budget")
    config = deepcopy(base_config)
    config.update(copy_data=True, preprocess_data=False, generate_report=False)
    config["exec"].update(timeout=300)
    agent = config["agent"]
    agent.update(num_workers=1, steps=2)
    agent["stages"] = {f"stage{i}_max_iters": n for i, n in enumerate((2, 2, 1, 1), 1)}
    agent["multi_seed_eval"]["num_seeds"] = 1
    agent["search"].update(num_drafts=1, max_debug_depth=1)
    for field, role in (("code", "code"), ("feedback", "feedback"), ("vlm_feedback", "vision")):
        agent[field].update(model=models[role], max_tokens=8192)
    agent["summary"] = {"model": models["feedback"], "temp": 0.3, "max_tokens": 4096}
    agent["select_node"] = {"model": models["feedback"], "temp": 0.3, "max_tokens": 4096}
    config["report"]["model"] = models["feedback"]
    idea = {"Name": task, "Title": package["question"], "Short Hypothesis": package["hypothesis"], "Related Work": "Synthetic integration experiment; approved evidence remains labeled by content level.", "Abstract": package["question"], "Experiments": package["method"], "Risk Factors and Limitations": "Small synthetic dataset; no claim of scientific novelty.", "Dataset": {"path": "/task/dataset.npz", "sha256": package["dataset_hash"], "version": package["dataset_version"]}, "Baseline": package["baseline"], "Required Metrics": package["metrics"], "Seeds": package["seeds"]}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / n).exists() for n in ("ideas.json", "bfts_config.yaml", "adapter.json")):
        raise ValueError("adapter output already exists")
    (output / "ideas.json").write_bytes(canonical([idea]))
    # JSON is a YAML subset, so upstream's yaml loader consumes these exact bytes.
    (output / "bfts_config.yaml").write_bytes(canonical(config))
    command = ["python", "/opt/scientist02/launch_scientist_bfts.py", "--load_ideas", "/task/ideas.json", "--idea_idx", "0", "--attempt_id", str(package["attempt"]), "--skip_writeup", "--skip_review", "--model_agg_plots", models["plot"]]
    metadata = {"command": command, "effective_config_hash": digest(canonical(config)), "upstream_config": "bfts_config.yaml", "model_mapping": package["config"].get("model_mapping", {}), "container_name": "research-" + task, "labels": {"research.task_id": task, "research.project_id": package["project_id"], "research.package_hash": package["package_hash"]}}
    (output / "adapter.json").write_bytes(canonical(metadata))
    return metadata
