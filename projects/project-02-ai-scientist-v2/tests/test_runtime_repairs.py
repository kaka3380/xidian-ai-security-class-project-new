"""Offline regression tests against actual project02 functions, without model imports."""
import ast
import os.path as osp
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
import logging
import sys
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]


def load_function(path, name, namespace, class_name=None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = tree.body
    if class_name:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == class_name).body
    node = next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class RuntimeRepairs(unittest.TestCase):
    def test_success_on_last_iteration_keeps_stage_alive(self):
        fn = load_function(SOURCE / "ai_scientist/treesearch/agent_manager.py",
                           "_check_stage_completion", {"Stage":SimpleNamespace,"logger":logging.getLogger("test")}, "AgentManager")
        stage = SimpleNamespace(name="1_initial", stage_number=1, max_iterations=2)
        for good in ([object()], []):
            manager = SimpleNamespace(journals={stage.name:SimpleNamespace(nodes=[object(),object()],good_nodes=good)},current_stage=stage)
            completed, reason = fn(manager, stage)
            self.assertTrue(completed)
            if good:
                self.assertIs(manager.current_stage, stage)
                self.assertNotIn("Failed", reason)
            else:
                self.assertIsNone(manager.current_stage)
                self.assertIn("Failed", reason)

    def test_best_export_excludes_buggy_candidate(self):
        noop = lambda *a, **k: None
        ns = {"Config":SimpleNamespace, "serialize":SimpleNamespace(dump_json=noop),
              "OmegaConf":SimpleNamespace(save=noop), "tree_export":SimpleNamespace(generate=noop)}
        fn = load_function(SOURCE / "ai_scientist/treesearch/utils/config.py", "save_run", ns)
        good = SimpleNamespace(id="valid",code="print('valid')")
        bad = SimpleNamespace(id="invalid",code="print('misleading metric')")
        journal = SimpleNamespace(get_best_node=lambda only_good, cfg: good if only_good else bad)
        with tempfile.TemporaryDirectory() as directory:
            fn(SimpleNamespace(log_dir=Path(directory)), journal, "stage_1")
            self.assertEqual((Path(directory)/"stage_1/best_node_id.txt").read_text(), "valid")
            self.assertFalse((Path(directory)/"stage_1/best_solution_invalid.py").exists())

    def test_constraints_reach_every_stage(self):
        path = SOURCE / "ai_scientist/treesearch/agent_manager.py"
        fn = load_function(path, "_curate_task_desc", {"Stage": SimpleNamespace}, "AgentManager")
        for plan in ("Use frozen responses only", ["Use frozen responses only"], [{"method": "Use frozen responses only"}]):
            manager = SimpleNamespace(_get_task_desc_str=lambda: "Base task\n", task_desc={
                "Experiments": plan, "Risk Factors and Limitations": ["Never fabricate outcomes"]})
            for stage in range(1, 5):
                with self.subTest(stage=stage, plan=plan):
                    prompt = fn(manager, SimpleNamespace(name=f"{stage}_test"))
                    self.assertIn("Use frozen responses only", prompt)
                    self.assertIn("Never fabricate outcomes", prompt)

    def test_missing_results_stop_before_plotting(self):
        path = SOURCE / "launch_scientist_bfts.py"
        calls = []
        ns = {"osp": osp, "shutil": shutil}
        load_function(path, "preserve_experiment_results", ns)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'")
        begin = next(i for i,n in enumerate(main.body) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id == "perform_experiments_bfts")
        end = next(i for i,n in enumerate(main.body) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id == "aggregate_plots")
        with tempfile.TemporaryDirectory() as directory:
            ns.update(idea_dir=directory, idea_config_path="unused", args=SimpleNamespace(model_agg_plots="unused"),
                      perform_experiments_bfts=lambda _: calls.append("experiment"),
                      save_token_tracker=lambda _: calls.append("save_usage"),
                      aggregate_plots=lambda **_: calls.append("paid_plotting"))
            with self.assertRaisesRegex(RuntimeError, "no result directory"):
                exec(compile(ast.Module(body=main.body[begin:end+1], type_ignores=[]), str(path), "exec"), ns)
            self.assertEqual(calls, ["experiment", "save_usage"])

    def test_results_are_preserved(self):
        fn = load_function(SOURCE / "launch_scientist_bfts.py", "preserve_experiment_results", {"osp":osp,"shutil":shutil})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "logs/0-run/experiment_results"
            source.mkdir(parents=True)
            (source / "metrics.json").write_text('{"accuracy":1.0}')
            fn(directory)
            self.assertEqual((source / "metrics.json").read_bytes(), (root / "experiment_results/metrics.json").read_bytes())

    def test_cleanup_only_targets_launcher_descendants(self):
        path = SOURCE / "launch_scientist_bfts.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'")
        start = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Import) and any(a.name == "psutil" for a in n.names))
        calls = []
        child = SimpleNamespace(
            send_signal=lambda value: calls.append("terminate_child"),
            kill=lambda: calls.append("kill_child"),
        )

        def wait_procs(processes, timeout):
            self.assertEqual(processes, [child])
            return [], processes

        def reject_global_scan(*args, **kwargs):
            self.fail("Cleanup must not scan unrelated host processes")

        fake_psutil = SimpleNamespace(
            Process=lambda: SimpleNamespace(children=lambda recursive: [child]),
            wait_procs=wait_procs,
            process_iter=reject_global_scan,
            NoSuchProcess=ProcessLookupError,
            AccessDenied=PermissionError,
            TimeoutExpired=TimeoutError,
        )
        block = ast.Module(body=main.body[start:-1], type_ignores=[])
        with patch.dict(sys.modules, {"psutil": fake_psutil}):
            exec(compile(block, str(path), "exec"), {})
        self.assertEqual(calls, ["terminate_child", "kill_child"])


if __name__ == "__main__":
    unittest.main()
