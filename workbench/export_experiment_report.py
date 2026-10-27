"""Export or reverify a deterministic, portable basic experiment report."""
import argparse
from pathlib import Path

from executor.protocol import canonical
from workbench.backend.services.injection_report import render_report, verify_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="record directory, or report directory with --verify")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--data", type=Path, help="independent gateway/task ledger root; required for regenerated answers")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_report(args.record, data=args.data)
        print(canonical(dict(status="passed", report_id=result["report_id"], review_status="pending_review")).decode())
    else:
        if args.output is None:
            parser.error("--output is required for export")
        path = render_report(args.record, args.output, data=args.data)
        result = verify_report(path.parent, data=args.data)
        print(canonical(dict(status="passed", index=str(path), report_id=result["report_id"], review_status="pending_review")).decode())
