"""Offline acceptance: compute and verify an actual CUDA matrix multiplication."""

import json
import platform
import sys
import time

import torch


def main():
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this is not a passing GPU check")
    torch.manual_seed(42)
    torch.set_num_threads(2)
    left = torch.randn(256, 256)
    right = torch.randn(256, 256)
    expected = left @ right
    start = time.perf_counter()
    actual = left.cuda() @ right.cuda()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    error = (actual.cpu() - expected).abs().max().item()
    torch.testing.assert_close(actual.cpu(), expected, rtol=1e-3, atol=1e-3)
    print(json.dumps({
        "status": "passed",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "gpu_total_bytes": torch.cuda.get_device_properties(0).total_memory,
        "matrix_shape": [256, 256],
        "max_absolute_error": error,
        "elapsed_seconds": elapsed,
        "model_requests": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
