from copy import deepcopy
import json
from pathlib import Path
import pytest
import yaml

from workbench.adapters.scientist02 import prepare_inputs


def test_effective_limits_and_exact_entrypoint(tmp_path):
    config = yaml.safe_load(Path("projects/project-02-ai-scientist-v2/bfts_config.yaml").read_text())
    original = deepcopy(config)
    package = {"engine": "scientist02", "schema_version": 2, "task_id": "task", "project_id": "p", "attempt": 1, "package_hash": "a" * 64, "question": "Question", "hypothesis": "Hypothesis", "method": "Method", "dataset_hash": "b" * 64, "dataset_version": 1, "baseline": "Majority", "metrics": ["accuracy"], "seeds": [42], "config": {"budget_cny": 5, "models": {role: "gpt-4o-2024-11-20" for role in ("code", "feedback", "vision", "plot")}}}
    metadata = prepare_inputs(package, config, tmp_path)
    actual = json.loads((tmp_path / "bfts_config.yaml").read_text())
    assert actual["agent"]["num_workers"] == 1
    assert actual["agent"]["stages"] == {"stage1_max_iters": 2, "stage2_max_iters": 2, "stage3_max_iters": 1, "stage4_max_iters": 1}
    assert actual["exec"]["timeout"] == 300
    assert actual["agent"]["multi_seed_eval"]["num_seeds"] == 1
    assert actual["agent"]["summary"]["model"] == "gpt-4o-2024-11-20"
    assert actual["agent"]["select_node"]["model"] == "gpt-4o-2024-11-20"
    assert "--skip_writeup" in metadata["command"] and "--skip_review" in metadata["command"]
    assert "--model_agg_plots" in metadata["command"]
    assert config == original
    package["config"]["models"].pop("vision")
    with pytest.raises(ValueError, match="explicit"):
        prepare_inputs(package, config, tmp_path / "bad")
