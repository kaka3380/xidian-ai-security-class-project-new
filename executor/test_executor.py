"""Linux state/launch boundary tests; uses isolated temporary runtime paths."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main as executor
from protocol import canonical, validate_package
from workbench import offline_client as client


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exchange = self.root / "exchange"
        self.patches = [patch.object(executor, "RUNS", self.root / "runs"), patch.object(executor, "EXCHANGE", self.exchange), patch.object(client, "EXCHANGE", self.exchange)]
        for item in self.patches:
            item.start()
        self.package = client.create_mock_package("task")
        self.folder = self.root / "runs/task"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def accept_without_process(self):
        self.folder.mkdir(parents=True)
        import shutil
        shutil.copytree(self.exchange / "task/inputs", self.folder / "inputs")
        executor.atomic(self.folder / "research_package.json", self.package)
        state = {"task_id": "task", "project_id": "offline-demo", "package_hash": validate_package(self.package, self.folder), "execution_status": "accepted", "process": None}
        executor.atomic(self.folder / "state.json", state)
        return state

    def test_crash_before_launch_is_interrupted_not_replayed(self):
        self.accept_without_process()
        with patch.object(executor.subprocess, "Popen") as launch:
            state = executor.submit("task", "offline-demo")
            self.assertEqual(state["execution_status"], "interrupted")
            launch.assert_not_called()

    def test_launch_failure_leaves_terminal_record(self):
        with patch.object(executor.subprocess, "Popen", side_effect=OSError("synthetic launch failure")):
            with self.assertRaises(OSError):
                executor.submit("task", "offline-demo")
        self.assertEqual(executor.status("task", "offline-demo")["execution_status"], "interrupted")

    def test_existing_task_survives_executor_upgrade(self):
        self.accept_without_process()
        with patch("protocol.snapshot_hash", return_value="new-source"), patch.object(executor.subprocess, "Popen") as launch:
            self.assertEqual(executor.submit("task", "offline-demo")["execution_status"], "interrupted")
            launch.assert_not_called()

    def test_result_written_before_state_recovers_without_replay(self):
        state = self.accept_without_process()
        executor.finish(self.folder, dict(state), "completed")
        executor.atomic(self.folder / "state.json", state)
        self.assertEqual(executor.status("task", "offline-demo")["execution_status"], "completed")

    def test_reused_pid_is_not_live(self):
        state = {"process": {"pid": 123, "start_ticks": "10", "boot_id": "old"}}
        for identity in ({"pid": 123, "start_ticks": "11", "boot_id": "old"}, {"pid": 123, "start_ticks": "10", "boot_id": "new"}, None):
            with patch.object(executor, "identity", return_value=identity):
                self.assertFalse(executor.alive(state))

    def test_symlink_input_rejected_before_acceptance(self):
        path = self.exchange / "task/inputs/evidence.txt"
        outside = self.root / "outside.txt"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            executor.submit("task", "offline-demo")
        self.assertFalse(self.folder.exists())

    def test_tampered_input_rejected_before_launch(self):
        (self.exchange / "task/inputs/evidence.txt").write_text("bad")
        with patch.object(executor.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                executor.submit("task", "offline-demo")
            launch.assert_not_called()
        self.assertFalse(self.folder.exists())

    def test_cannot_collect_another_projects_task(self):
        self.accept_without_process()
        with self.assertRaisesRegex(ValueError, "ownership"):
            executor.collect("task", "other")


if __name__ == "__main__":
    unittest.main()
