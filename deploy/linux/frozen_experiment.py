"""Approved experiment template: read the immutable dataset, never regenerate it."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch

dataset = Path('/task/dataset.npz')
data = np.load(dataset, allow_pickle=False)
assert data['x_train'].shape == (192, 8) and data['x_test'].shape == (64, 8)
assert not np.intersect1d(data['train_indices'], data['test_indices']).size
torch.set_num_threads(1)
torch.manual_seed(42)
model = torch.nn.Sequential(torch.nn.Linear(8, 16), torch.nn.ReLU(), torch.nn.Linear(16, 1))
optimizer = torch.optim.Adam(model.parameters(), lr=0.03)
x = torch.from_numpy(data['x_train'])
y = torch.from_numpy(data['y_train']).reshape(-1, 1)
start = time.perf_counter()
for _ in range(40):
    optimizer.zero_grad()
    loss = torch.nn.functional.binary_cross_entropy_with_logits(model(x), y)
    loss.backward()
    optimizer.step()
duration = time.perf_counter() - start
with torch.no_grad():
    predictions = (model(torch.from_numpy(data['x_test'])).flatten() >= 0).numpy().astype(int)
baseline = int(data['y_train'].mean() >= 0.5)
metrics = dict(accuracy=float(np.mean(predictions == data['y_test'])), baseline_accuracy=float(np.mean(baseline == data['y_test'])), train_seconds=duration, seed=42, dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(), predictions=predictions.tolist())
Path('metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
print(json.dumps(metrics))
