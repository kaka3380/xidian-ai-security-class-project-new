"""Bounded 02 benchmark runner. Frozen analysis never creates model requests."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from executor.protocol import canonical
from workbench.backend.services.injection_experiment import recompute, validate_inputs, source_hash, request_for


def run(folder, call=None):
    folder = Path(folder)
    package = json.loads((folder / "research_package.json").read_bytes())
    inputs = json.loads((folder / "experiment-input.json").read_bytes())
    value = validate_inputs(package, inputs)
    if source_hash() != package["source_snapshot_hash"]:
        raise ValueError("running source differs from approved snapshot")
    output = folder / "experiments"
    output.mkdir(exist_ok=True)
    responses = inputs["responses"]
    if value["mode"] == "regenerate":
        if call is None:
            def call(row):
                request = urllib.request.Request(os.environ["EXPERIMENT_GATEWAY_URL"], data=canonical(request_for(package, row)),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["EXPERIMENT_TOKEN"],
                             "X-Experiment-Grant": os.environ["EXPERIMENT_GRANT"], "X-Experiment-Task": package["task_id"], "X-Experiment-Sample": row["sample_id"]})
                try:
                    with urllib.request.urlopen(request, timeout=min(180, value["timeout_seconds"])) as reply:
                        return json.load(reply)
                except urllib.error.HTTPError as error:
                    data = json.load(error)
                    if not data.get("workbench_request_id"):
                        raise ValueError("gateway rejected request; budget or contract limit") from error
                    return data
        responses = []
        for row in inputs["dataset"]:
            data = call(row)
            with (output / "raw-responses.jsonl").open("ab") as stream:
                stream.write(canonical(dict(sample_id=row["sample_id"], response=data)) + b"\n")
            failed = "error" in data
            responses.append(dict(sample_id=row["sample_id"], condition=row["condition"], status="error" if failed else "ok",
                answer="" if failed else data["choices"][0]["message"]["content"], model=value["provider_model"] if failed else data["model"],
                request_id=data["workbench_request_id"]))
    (output / "responses.json").write_bytes(canonical(responses))
    (output / "metrics.json").write_bytes(canonical(recompute(package, inputs["dataset"], responses)))
    (output / "figures.json").write_bytes(canonical([]))


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "/task")
