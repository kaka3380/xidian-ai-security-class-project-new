"""Publish immutable approved packages and use the existing WSL executor protocol."""
import json
from pathlib import Path
import shutil
import tempfile

from executor.protocol import canonical, confined, digest
from workbench import offline_client


class Executor02:
    identity = "wsl:Ubuntu-24.04:research:scientist02"

    def __init__(self, store):
        self.store = store

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
        return offline_client.invoke("submit", task["task_id"], task["project_id"])

    def status(self, task):
        return offline_client.invoke("status", task["task_id"], task["project_id"])

    def cancel(self, task):
        return offline_client.invoke("cancel", task["task_id"], task["project_id"])

    def collect(self, task):
        data = self.store.root / "experiments"
        result = offline_client.collect_verified(task["task_id"], task["project_id"], data=data)
        return data / task["project_id"] / task["task_id"], result
