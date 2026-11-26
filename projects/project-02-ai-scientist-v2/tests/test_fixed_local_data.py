import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "ai_scientist" / "treesearch" / "agent_manager.py"
)
IDEA = json.dumps({
    "Title": "Local data comparison",
    "Abstract": "Compare two settings on the same local data.",
    "Short Hypothesis": "The settings may affect accuracy.",
    "Experiments": "Reuse the same epoch-by-epoch sequence of training indices.",
    "Risk Factors and Limitations": "One synthetic dataset.",
})


def load_agent_manager():
    # The stage planner can be exercised without loading Torch or model clients.
    root = "scientist02_stage_fixture"
    dependencies = {
        f"{root}.parallel_agent": SimpleNamespace(ParallelAgent=object),
        f"{root}.journal": SimpleNamespace(Journal=object, Node=object),
        f"{root}.backend": SimpleNamespace(query=Mock(), FunctionSpec=Mock()),
        f"{root}.utils.serialize": SimpleNamespace(parse_markdown_to_dict=Mock()),
        f"{root}.utils.metric": SimpleNamespace(WorstMetricValue=object),
        "rich": SimpleNamespace(print=print),
    }
    spec = importlib.util.spec_from_file_location(f"{root}.agent_manager", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {**dependencies, spec.name: module}):
        spec.loader.exec_module(module)
    return module.AgentManager


class StudyDataPolicyTest(unittest.TestCase):
    def test_fixed_local_mode_uses_one_dataset_through_all_stages(self):
        manager = self.manager(fixed_local_data=True)
        self.assertNotIn("HuggingFace", " ".join(manager.main_stage_goals.values()))
        self.assertIn("fixed local dataset", manager.main_stage_goals[2])
        self.assertIn("fixed local dataset", manager.main_stage_goals[3])
        self.assertIn("same datasets", manager.main_stage_goals[4])
        self.assertIn("sequence of training indices", manager._curate_task_desc(manager.current_stage))

    def test_default_and_older_configs_keep_the_original_stage_goals(self):
        for options in ({}, {"fixed_local_data": False}):
            with self.subTest(options=options):
                manager = self.manager(**options)
                self.assertIn("HuggingFace", manager.main_stage_goals[2])
                self.assertIn("HuggingFace", manager.main_stage_goals[3])

    @staticmethod
    def manager(**options):
        agent = SimpleNamespace(steps=1, stages=SimpleNamespace(), search=SimpleNamespace(num_drafts=1))
        cfg = SimpleNamespace(agent=agent, experiment=SimpleNamespace(**options))
        return load_agent_manager()(IDEA, cfg, Path("."))
