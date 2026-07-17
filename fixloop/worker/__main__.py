from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import run
from .errors import WorkerError


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a two-commit fixloop branch")
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--issue-number", required=True, type=int)
    parser.add_argument("--issue-file", required=True, type=Path)
    parser.add_argument("--reason-codes", nargs="*", default=[])
    args = parser.parse_args()
    try:
        branch = run(args.target, args.issue_number, args.issue_file.read_text(), args.reason_codes)
    except WorkerError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "detail": exc.detail}))
        return 1
    print(json.dumps({"status": "completed", "branch": branch}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
