"""cli.py

Command-line entry point for the odps-sql-review package.

Two equivalent invocations are supported:

* ``python -m odps_sql_review path/to/query.sql``
* ``./bin/odps-sql-review path/to/query.sql``

The CLI never connects to ODPS; it only performs static analysis and
prints a Markdown or JSON report.  Use ``--`` as the file path to read
SQL from standard input.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .report import render_json, render_markdown
from .static_check import run_static_check
from .validation_sql import generate_validation_sql


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odps-sql-review",
        description=(
            "Static review for ODPS / MaxCompute SQL. "
            "Identifies correctness risks (partition pruning, JOIN safety, "
            "metric duplication, INSERT OVERWRITE issues) and performance "
            "risks (COUNT DISTINCT, GROUP BY skew, MAPJOIN candidates, "
            "long-period MAU/YAU optimization)."
        ),
    )
    parser.add_argument(
        "sql_path",
        nargs="?",
        help="Path to a SQL file. Use '-' to read from STDIN.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "--output",
        help="Write the report to the given file. Defaults to STDOUT.",
    )
    parser.add_argument(
        "--table-profile",
        help="Optional path to a YAML file describing tables (partition_cols, "
        "grain, unique_keys, size_level, table_type).",
    )
    parser.add_argument(
        "--generate-validation-sql",
        action="store_true",
        help="Print only the validation SQL templates instead of the full report.",
    )
    parser.add_argument(
        "--focus",
        choices=("correctness", "performance", "all"),
        default="all",
        help="Filter findings to a single area (default: all).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra diagnostic information to stderr.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"odps-sql-review {__version__}",
    )
    return parser


def _read_sql(path: Optional[str]) -> str:
    """Read SQL from a file path, ``-`` for STDIN, or print an error."""

    if not path:
        print(
            "[odps-sql-review] error: please provide a SQL file path or '-' to read from STDIN.",
            file=sys.stderr,
        )
        sys.exit(2)
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print(f"[odps-sql-review] failed to read SQL file: {exc}", file=sys.stderr)
        sys.exit(2)


def _write_output(text: str, output_path: Optional[str]) -> None:
    """Write the rendered report to STDOUT or to a file."""

    if not output_path:
        # Use sys.stdout.write rather than print to avoid an extra newline.
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
    except OSError as exc:
        print(f"[odps-sql-review] failed to write output: {exc}", file=sys.stderr)
        sys.exit(2)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    sql = _read_sql(args.sql_path)
    if args.verbose:
        print(
            f"[odps-sql-review] read {len(sql)} characters of SQL.",
            file=sys.stderr,
        )

    result = run_static_check(
        sql,
        table_profile_path=args.table_profile,
        focus=args.focus,
    )

    if args.generate_validation_sql:
        snippets = generate_validation_sql(result.tables, result.joins, result.metrics)
        if args.format == "json":
            text = json.dumps(snippets, ensure_ascii=False, indent=2)
        else:
            from .validation_sql import render_validation_sql_markdown

            text = "# 上线前验数 SQL 模板\n\n" + render_validation_sql_markdown(snippets)
    else:
        if args.format == "json":
            text = render_json(result)
        else:
            text = render_markdown(result)

    _write_output(text, args.output)

    # Exit code 1 only when a blocking issue is reported.  This makes
    # it easy to call from CI:  if odps-sql-review query.sql; then ...
    return 1 if result.blocking else 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
