#!/usr/bin/env python3
"""scripts/generate_validation_sql.py

Generate the validation SQL templates for a given SQL file.  Output is
either Markdown (default) or JSON.  These templates are *not executed*
- they are meant to be reviewed and then run by the analyst inside ODPS
Studio / DataWorks.
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

from odps_sql_review.parser import parse  # noqa: E402
from odps_sql_review.extract_joins import extract_joins  # noqa: E402
from odps_sql_review.extract_metrics import extract_metrics  # noqa: E402
from odps_sql_review.extract_tables import extract_tables  # noqa: E402
from odps_sql_review.validation_sql import (  # noqa: E402
    generate_validation_sql,
    render_validation_sql_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate validation (验数) SQL templates for an ODPS SQL file.",
    )
    parser.add_argument("--input", required=True, help="Path to the SQL file (or '-' for STDIN).")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    if args.input == "-":
        sql = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as fh:
            sql = fh.read()

    parsed = parse(sql)
    snippets = generate_validation_sql(
        extract_tables(parsed),
        extract_joins(parsed),
        extract_metrics(parsed),
    )
    if args.format == "json":
        print(json.dumps(snippets, ensure_ascii=False, indent=2))
    else:
        print("# 上线前验数 SQL 模板\n")
        print(render_validation_sql_markdown(snippets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
