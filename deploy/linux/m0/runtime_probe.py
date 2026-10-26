"""Offline runtime inventory and optional real journal reconstruction; no LLM calls."""
import argparse
import ast
import json
from pathlib import Path
import subprocess

from executor.protocol import canonical, digest, file_hash


def probe(inputs=None, manifest=None):
    lock = json.loads(Path("/opt/m0/source-lock.json").read_text())
    payload = {k: v for k, v in lock.items() if k != "source_snapshot_hash"}
    if digest(canonical(payload)) != lock["source_snapshot_hash"]:
        raise ValueError("source lock hash mismatch")
    for item in lock["files"]:
        if item["target"] is not None:
            path = Path(item["target"])
            if file_hash(path) != item["sha256"]:
                raise ValueError("runtime overlay hash mismatch: " + item["target"])
            if path.suffix == ".py":
                ast.parse(path.read_text(encoding="utf-8"))
    tools = {}
    for command in (["pdflatex", "--version"], ["bibtex", "--version"], ["chktex", "--version"],
                    ["pdftotext", "-v"], ["pdftoppm", "-v"]):
        check = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=15, check=True)
        tools[command[0]] = check.stdout.splitlines()[0]
    # Inventory the actual inherited source tree as well as the explicit patches.
    # Exclude runtime output/caches, never traverse credentials or the host.
    source = Path("/opt/scientist02")
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_file() and not any(part in {".git", "__pycache__", ".venv", "logs", "experiments", "experiment_results"} for part in relative.parts):
            files[relative.as_posix()] = file_hash(path)
    report = {"status": "passed", "scope": "offline runtime only; model and scientific quality unverified",
              "base_image_id": lock["base_image_id"], "source_snapshot_hash": lock["source_snapshot_hash"],
              "runtime_source_hash": digest(canonical(files)), "runtime_source_files": files, "tools": tools,
              "model_requests": 0}
    if inputs is not None:
        from workbench.bridges.scientist02.journal import reconstruct_journals
        lock = json.loads(manifest.read_text())
        journals = reconstruct_journals(inputs, lock["journal_inputs"])
        report["reconstructed_stages"] = [{"stage": name, "nodes": len(journal.nodes),
            "edges": sum(n.parent is not None for n in journal.nodes)} for name, journal in journals]
        for _, journal in journals:
            for node in journal.nodes:
                if node.parent is not None and node not in node.parent.children:
                    raise ValueError("reconstructed parent/child mismatch")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if (args.inputs is None) != (args.manifest is None):
        parser.error("--inputs and --manifest must be supplied together")
    print(json.dumps(probe(args.inputs, args.manifest), sort_keys=True))
