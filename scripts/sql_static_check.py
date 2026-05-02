#!/usr/bin/env python3
"""scripts/sql_static_check.py

Thin wrapper that lets users (or AI agents that look for ``scripts/``
entry points) call ``odps_sql_review`` without remembering the module
syntax.

Usage:
    python scripts/sql_static_check.py --input examples/bad_left_join.sql
    python scripts/sql_static_check.py --input examples/bad_left_join.sql --format json

The actual logic lives in ``odps_sql_review.cli`` so this script stays
trivially small.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the package can be found when the script is executed directly
# from a checkout (without installing it).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from odps_sql_review.cli import main as cli_main  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the odps-sql-review static check on a SQL file.",
    )
    parser.add_argument("--input", required=True, help="Path to the SQL file (or '-' for STDIN).")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", help="Optional output file path.")
    parser.add_argument("--table-profile", help="Optional table profile YAML path.")
    parser.add_argument(
        "--focus",
        choices=("correctness", "performance", "all"),
        default="all",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    forwarded = [args.input, "--format", args.format, "--focus", args.focus]
    if args.output:
        forwarded.extend(["--output", args.output])
    if args.table_profile:
        forwarded.extend(["--table-profile", args.table_profile])
    return cli_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
