"""Publish approved packages and dispatch them over the executor protocol."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from executor.protocol import canonical, confined, digest
from workbench import offline_client


class Executor02:
    LEGACY_IDENTITY = "wsl:Ubuntu-24.04:research:scientist02"

    def __init__(self, store, *, backend=None):
        self.store = store
        self.backend = backend or os.environ.get("WORKBENCH_EXECUTOR_BACKEND", "docker")
        if self.backend not in {"docker", "wsl"}:
            raise ValueError("WORKBENCH_EXECUTOR_BACKEND must be docker or wsl")
        self.context = os.environ.get("WORKBENCH_DOCKER_CONTEXT")
        self.endpoint = None
        self.daemon_id = None
        self._wsl_settings = None

    def _docker_context(self):
        if not self.context:
            if os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"):
                raise ValueError("use a named local Docker context via WORKBENCH_DOCKER_CONTEXT")
            result = subprocess.run(["docker", "context", "show"], capture_output=True, text=True, timeout=5, check=True)
            self.context = result.stdout.strip()
            if not self.context:
                raise ValueError("Docker context unavailable")
        return self.context

    def _docker_target(self):
        if self.endpoint is None:
            if sys.platform not in {"win32", "linux"}:
                raise ValueError("the Docker executor currently supports Windows and Linux hosts")
            context = self._docker_context()
            raw = subprocess.check_output(["docker", "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"], text=True, timeout=5)
            endpoint = raw.strip()
            if not (endpoint.startswith("unix:///") or re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_.-]+", endpoint)):
                raise ValueError("workbench requires a local Unix socket or Windows named pipe Docker context")
            daemon_id = subprocess.check_output(offline_client.docker_host_args(endpoint) + ["info", "--format", "{{.ID}}"],
                                                env=offline_client.docker_host_environment(), text=True, timeout=10).strip()
            if not daemon_id:
                raise ValueError("Docker daemon identity unavailable")
            self.endpoint, self.daemon_id = endpoint, daemon_id
        return self.endpoint, self.daemon_id

    @property
    def identity(self):
        if self.backend == "wsl":
            settings = self.wsl_settings()
            if settings == ("Ubuntu-24.04", "research", "/home/research/research-runtime",
                            "/mnt/e/work/ai-security-class-project/workbench/exchange"):
                return self.LEGACY_IDENTITY
            return "wsl:" + digest(canonical(settings)) + ":scientist02"
        endpoint, daemon_id = self._docker_target()
        return "docker:" + digest(canonical([endpoint, daemon_id])) + ":scientist02"

    def for_identity(self, identity):
        if isinstance(identity, str) and identity.startswith("wsl:") and self.backend != "wsl":
            return Executor02(self.store, backend="wsl")
        return self

    def wsl_settings(self):
        if self._wsl_settings is None:
            self._wsl_settings = offline_client.wsl_settings()
        return self._wsl_settings

    def _invoke(self, operation, task, project):
        if self.backend == "wsl":
            return offline_client.invoke(operation, task, project, settings=self.wsl_settings())
        endpoint, daemon_id = self._docker_target()
        return offline_client.invoke_docker(operation, task, project, runtime=self.store.root / "docker-executor", endpoint=endpoint, daemon_id=daemon_id)

    def publish(self, task):
        package, files, documents, plan = self.store.experiment_context(task)
        folder = offline_client.EXCHANGE / task["task_id"]
        offline_client.EXCHANGE.mkdir(parents=True, exist_ok=True)
        content = {"research_package.json": canonical(package), "approved-plan.json": canonical(plan),
                   "approved-documents.json": canonical(documents), "experiment-input.json": canonical(task["input"]["experiment_input"]), **files}
        from workbench.backend.services.injection_experiment import source_inventory
        inventory = source_inventory()
        root = Path(__file__).resolve().parents[2]
        content["source-snapshot.json"] = canonical(inventory)
        for name, checksum in inventory.items():
            data = (root / name).read_bytes()
            if digest(data) != checksum:
                raise ValueError("source changed while freezing execution snapshot")
            content["runtime/" + name] = data
        if package["config"]["experiment"]["mode"] == "regenerate":
            from workbench.backend.services.experiment_budget import ExperimentBudgets
            budgets = ExperimentBudgets(self.store.root)
            budgets.bind(task["input"]["grant_id"], package, task["input"]["experiment_input"]["dataset"])
            gateway = json.loads((self.store.root / "model-session/gateway.json").read_bytes())
            if gateway.get("experiment_data") != str(self.store.root) or type(gateway.get("port")) is not int or not 1 <= gateway["port"] <= 65535:
                raise ValueError("gateway must be explicitly bound to this workbench budget database")
            if gateway.get("provider") != package["config"]["experiment"].get("provider"):
                raise ValueError("gateway provider does not match the approved plan")
            # Only a restricted grant token, never the provider key or legacy gateway token.
            content["gateway-access.json"] = canonical(dict(grant_id=task["input"]["grant_id"], token=budgets.credential(task["input"]["grant_id"]), port=gateway["port"]))
        if folder.exists():
            if any(confined(folder, name).read_bytes() != data for name, data in content.items()):
                raise ValueError("immutable exchange package changed")
            return package
        stage = Path(tempfile.mkdtemp(prefix=".injection-", dir=offline_client.EXCHANGE))
        try:
            for name, data in content.items():
                target = confined(stage, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            offline_client.publish_directory(stage, folder)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return package

    def submit(self, task):
        return self._invoke("submit", task["task_id"], task["project_id"])

    def status(self, task):
        return self._invoke("status", task["task_id"], task["project_id"])

    def cancel(self, task):
        return self._invoke("cancel", task["task_id"], task["project_id"])

    def collect(self, task):
        data = self.store.root / "experiments"
        result = offline_client.collect_verified(task["task_id"], task["project_id"], data=data, request=self._invoke)
        return data / task["project_id"] / task["task_id"], result
