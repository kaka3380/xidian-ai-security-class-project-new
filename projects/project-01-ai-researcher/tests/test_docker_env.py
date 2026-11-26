import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "research_agent" / "inno" / "environment" / "docker_env.py"
)


def load_docker_env():
    constants = ModuleType("constant")
    constants.BASE_IMAGES = "sha256:" + "a" * 64
    constants.AI_USER = ""
    constants.GITHUB_AI_TOKEN = ""
    constants.GPUS = ""
    constants.PLATFORM = "linux/amd64"
    spec = importlib.util.spec_from_file_location("docker_env_fixture", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"constant": constants, spec.name: module}):
        spec.loader.exec_module(module)
    return module


class DockerEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.module = load_docker_env()
        self.config = self.module.DockerConfig("course-demo", "workspace", 18766)

    def test_unmanaged_stop_preserves_legacy_behavior(self):
        with patch.dict(os.environ, {}, clear=True):
            environment = self.module.DockerEnv(self.config)
        with patch.object(self.module.subprocess, "run", return_value=Mock(returncode=0)) as docker:
            environment.stop_container()
        docker.assert_called_once_with(
            ["docker", "stop", "course-demo"], capture_output=True, text=True
        )

    def test_managed_stop_rejects_a_container_from_another_task(self):
        environment = self.managed_environment()
        foreign = self.inspect_result("task-b", running=True)
        with patch.object(self.module.subprocess, "run", return_value=foreign) as docker:
            with self.assertRaisesRegex(ValueError, "different task"):
                environment.stop_container()
        docker.assert_called_once_with(
            ["docker", "inspect", "course-demo"], capture_output=True, text=True, check=True
        )

    def test_managed_stop_checks_ownership_and_confirms_the_stop(self):
        environment = self.managed_environment()
        results = [
            self.inspect_result("task-a", running=True),
            Mock(returncode=0),
            self.inspect_result("task-a", running=False),
        ]
        with patch.object(self.module.subprocess, "run", side_effect=results) as docker:
            environment.stop_container()
        self.assertEqual(
            [invocation.args[0] for invocation in docker.call_args_list],
            [["docker", action, "course-demo"] for action in ("inspect", "stop", "inspect")],
        )

    def managed_environment(self):
        with patch.dict(
            os.environ,
            {"RESEARCH_TASK_ID": "task-a", "RESEARCH_PROJECT_ID": "course"},
            clear=True,
        ):
            return self.module.DockerEnv(self.config)

    @staticmethod
    def inspect_result(task, *, running):
        return Mock(stdout=json.dumps([{
            "Config": {"Labels": {"research.task_id": task, "research.project_id": "course"}},
            "State": {"Running": running},
        }]))
