import importlib.util
import json
from pathlib import Path
import sys
import types
from unittest.mock import Mock
import pytest


@pytest.fixture
def module(monkeypatch):
    constant = types.ModuleType("constant")
    for key, value in dict(BASE_IMAGES="sha256:" + "a" * 64, AI_USER="", GITHUB_AI_TOKEN="", GPUS="all", PLATFORM="linux/amd64").items():
        setattr(constant, key, value)
    monkeypatch.setitem(sys.modules, "constant", constant)
    path = Path("projects/project-01-ai-researcher/research_agent/inno/environment/docker_env.py")
    spec = importlib.util.spec_from_file_location("tested_docker_env", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setenv("RESEARCH_TASK_ID", "task")
    monkeypatch.setenv("RESEARCH_PROJECT_ID", "project")
    return module


def test_managed_container_is_nonroot_limited_and_loopback(module, monkeypatch, tmp_path):
    run = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(module.subprocess, "run", run)
    env = module.DockerEnv(module.DockerConfig("research-task", "workplace", 7020, local_root=str(tmp_path)))
    monkeypatch.setattr(env, "wait_for_container_ready", lambda **kw: True)
    env.init_container()
    command = run.call_args_list[-1].args[0]
    assert command[command.index("--user") + 1] == "1000:1000"
    assert command[command.index("--restart") + 1] == "no"
    assert "127.0.0.1:7020:8000" in command
    assert "research.task_id=task" in command
    assert "--memory" in command and "--pids-limit" in command
    assert "--userns=host" not in command


def test_foreign_container_cannot_be_stopped(module, monkeypatch, tmp_path):
    run = Mock(return_value=types.SimpleNamespace(returncode=0, stdout=json.dumps([{"Config": {"Labels": {"research.task_id": "other"}}, "State": {"Running": True}}]), stderr=""))
    monkeypatch.setattr(module.subprocess, "run", run)
    env = module.DockerEnv(module.DockerConfig("research-task", "workplace", 7020, local_root=str(tmp_path)))
    with pytest.raises(ValueError, match="different task"):
        env.stop_container()
    assert all("stop" not in call.args[0] for call in run.call_args_list)
