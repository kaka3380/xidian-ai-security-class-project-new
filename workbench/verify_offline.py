"""Run the synthetic workflow acceptance in an isolated local directory."""
import argparse
from pathlib import Path
import shutil
import tempfile

import pytest

from workbench.backend.services.injection_report import verify_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tests = Path(__file__).resolve().parent / "tests"
    with tempfile.TemporaryDirectory(prefix="validation-", dir=output) as workspace:
        base = Path(workspace) / "pytest"
        exit_code = pytest.main([
            str(tests / "test_workbench_m1.py") + "::test_restart_polls_recorded_task_without_resubmission",
            str(tests / "test_injection_report.py") + "::test_export_portable_frozen_report_is_reproducible_and_stays_pending",
            "--basetemp", str(base), "-q",
        ])
        if exit_code:
            return exit_code
        (report,) = base.glob("test_export_portable_frozen_re[0-9]*/report")
        destination = shutil.copytree(report, output / "report")
        verify_report(destination)
    print(f"Verified synthetic report: {destination / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
