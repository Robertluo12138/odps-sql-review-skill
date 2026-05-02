#!/usr/bin/env python3
"""scripts/extract_joins.py

Print every JOIN clause discovered in a SQL file along with detected
risks (LEFT-JOIN-becomes-INNER, ON 1=1, OR in ON, missing keys, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from odps_sql_review.extract_joins import extract_joins_from_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract JOIN clauses from an ODPS SQL file.")
    parser.add_argument("--input", required=True, help="Path to the SQL file (or '-' for STDIN).")
    args = parser.parse_args()

    if args.input == "-":
        sql = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as fh:
            sql = fh.read()

    print(json.dumps(extract_joins_from_text(sql), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
