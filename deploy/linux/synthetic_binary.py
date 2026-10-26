"""Frozen dataset and independently reproducible reference for the approved smoke experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np


def make_dataset(path):
    rng = np.random.default_rng(42)
    x = rng.normal(size=(256, 8)).astype(np.float32)
    y = (x[:, 0] + 0.5 * x[:, 1] > 0).astype(np.float32)
    np.savez(path, x_train=x[:192], y_train=y[:192], x_test=x[192:], y_test=y[192:], train_indices=np.arange(192), test_indices=np.arange(192, 256))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference(dataset, output):
    import torch
    torch.set_num_threads(1)
    torch.manual_seed(42)
    data = np.load(dataset)
    x = torch.from_numpy(data["x_train"])
    y = torch.from_numpy(data["y_train"]).reshape(-1, 1)
    model = torch.nn.Sequential(torch.nn.Linear(8, 16), torch.nn.ReLU(), torch.nn.Linear(16, 1))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.03)
    start = time.perf_counter()
    for _ in range(40):
        optimizer.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(model(x), y)
        loss.backward()
        optimizer.step()
    duration = time.perf_counter() - start
    with torch.no_grad():
        predictions = (model(torch.from_numpy(data["x_test"])).flatten() >= 0).numpy().astype(int)
    baseline = int(data["y_train"].mean() >= 0.5)
    metrics = {"accuracy": float(np.mean(predictions == data["y_test"])), "baseline_accuracy": float(np.mean(baseline == data["y_test"])), "train_seconds": duration, "seed": 42, "dataset_sha256": hashlib.sha256(Path(dataset).read_bytes()).hexdigest(), "predictions": predictions.tolist(), "kind": "independent_reference_not_upstream_agent_result"}
    Path(output).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["generate", "reference"])
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", default="reference-metrics.json")
    args = parser.parse_args()
    print(json.dumps({"dataset_sha256": make_dataset(args.dataset)} if args.operation == "generate" else reference(args.dataset, args.output)))
