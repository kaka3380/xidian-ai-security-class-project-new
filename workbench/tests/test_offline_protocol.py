import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from executor.protocol import canonical, confined, digest, validate_package
from workbench import offline_client as client


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exchange = self.root / "exchange"
        self.patches = [patch.object(client, "EXCHANGE", self.exchange), patch.object(client, "DATA", self.root / "data")]
        for item in self.patches:
            item.start()
        self.package = client.create_mock_package("test-task")
        self.folder = self.exchange / "test-task"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_canonical_hash_and_valid_fixture(self):
        self.assertEqual(validate_package(self.package, self.folder), digest(canonical(self.package)))
        self.assertEqual(digest(canonical({"b": 1, "a": 2})), digest(canonical({"a": 2, "b": 1})))

    def test_paths_and_ids(self):
        for name in ("../outside", "/etc/passwd", "a/../../x", "C:/secret", "a\\x", "a/./b", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                confined(self.folder, name)
        for task in ("../x", "x;touch", "", "a/b", "a b"):
            with self.subTest(task=task), self.assertRaises(ValueError):
                client.invoke("status", task, "project")

    def test_tampered_input(self):
        (self.folder / "inputs/evidence.txt").write_text("modified")
        with self.assertRaisesRegex(ValueError, "hash"):
            validate_package(self.package, self.folder)

    def test_reject_real_engine_and_bad_config(self):
        mutations = [("engine", "scientist02"), ("config_hash", "0" * 64), ("source_snapshot_hash", "0" * 64), ("attempt", True), ("metrics", ["accuracy"])]
        for key, value in mutations:
            package = dict(self.package, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_package(package, self.folder)

    def test_duplicate_input(self):
        package = copy.deepcopy(self.package)
        package["documents"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_package(package, self.folder)

    def test_fixed_argument_list(self):
        with patch.object(client.subprocess, "run") as run:
            run.return_value.stdout = b'{"engines":["mock"]}'
            run.return_value.returncode = 0
            client.invoke("status", "safe", "project")
            args, kwargs = run.call_args
            self.assertIsInstance(args[0], list)
            self.assertEqual(args[0][-3:], ["status", "safe", "project"])
            self.assertNotIn("shell", kwargs)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows sharing retry")
    def test_transient_windows_sharing_violation(self):
        with patch.object(client.os, "rename", side_effect=[PermissionError("busy"), None]) as rename:
            client.publish_directory(self.root / "staging", self.root / "absent")
            self.assertEqual(rename.call_count, 2)

    def prepare_result(self, **changes):
        result = {"task_id": "test-task", "project_id": "offline-demo", "package_hash": digest(canonical(self.package)), "engine": "mock", "validation_status": "passed", "execution_status": "completed", "metrics": {"input_bytes": 56}, "artifacts": [{"path": "artifacts/data.txt", "sha256": digest(b"output")}]}
        result.update(changes)
        folder = self.folder / "result"
        (folder / "artifacts").mkdir(parents=True, exist_ok=True)
        (folder / "artifacts/data.txt").write_bytes(b"output")
        (folder / "result.json").write_bytes(canonical(result))
        receipt = {"task_id": "test-task", "project_id": "offline-demo", "package_hash": result["package_hash"], "result_sha256": digest(canonical(result))}
        return receipt

    def test_receive_and_idempotent_collect(self):
        receipt = self.prepare_result()
        with patch.object(client, "invoke", return_value=receipt):
            result = client.collect_verified("test-task", "offline-demo")
            self.assertEqual(result, client.collect_verified("test-task", "offline-demo"))
        self.assertTrue((self.root / "data/offline-demo/test-task/result.json").exists())

    def test_reject_corrupt_artifact(self):
        receipt = self.prepare_result()
        (self.folder / "result/artifacts/data.txt").write_bytes(b"corrupt")
        with patch.object(client, "invoke", return_value=receipt), self.assertRaisesRegex(ValueError, "artifact hash"):
            client.collect_verified("test-task", "offline-demo")
        self.assertFalse((self.root / "data/offline-demo/test-task").exists())

    def test_reject_corrupt_result(self):
        receipt = self.prepare_result()
        (self.folder / "result/result.json").write_text("{}")
        with patch.object(client, "invoke", return_value=receipt), self.assertRaisesRegex(ValueError, "result hash"):
            client.collect_verified("test-task", "offline-demo")

    def test_reject_cross_project(self):
        receipt = self.prepare_result(project_id="another-project")
        with patch.object(client, "invoke", return_value=receipt), self.assertRaisesRegex(ValueError, "ownership"):
            client.collect_verified("test-task", "offline-demo")

    def test_missing_metrics_cannot_pass(self):
        receipt = self.prepare_result(metrics={})
        with patch.object(client, "invoke", return_value=receipt), self.assertRaisesRegex(ValueError, "metrics"):
            client.collect_verified("test-task", "offline-demo")


if __name__ == "__main__":
    unittest.main()
