"""extract_tables.py

Extract source tables, aliases, and partition-filter hints from a SQL
statement.

Returned data is plain Python (lists / dicts) so it serializes cleanly
to JSON for downstream tooling and AI agents.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set

from .parser import ParsedSQL, parse
from .utils import PARTITION_COLUMN_NAMES, looks_like_partition_column, normalize_whitespace


# Match an identifier optionally qualified by a project / schema, e.g.
# my_project.my_db.my_table or just my_table.
_TABLE_IDENT = r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*){0,2}"
# FROM / JOIN clauses introduce a table reference.  We allow optional
# parentheses but only capture identifier-style references; subqueries
# are skipped because they don't have a single canonical table name.
_FROM_JOIN_RE = re.compile(
    r"\b(from|join)\s+(?!\()(" + _TABLE_IDENT + r")\s*(?:as\s+)?([A-Za-z_][\w]*)?",
    re.IGNORECASE,
)


@dataclass
class TableReference:
    """A single table reference extracted from a SQL statement."""

    name: str
    alias: Optional[str]
    is_cte: bool
    has_partition_filter: Optional[bool]
    partition_filter_columns: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _find_partition_filters(text: str, alias: Optional[str], table: str) -> List[str]:
    """Look for filter expressions that reference a partition column.

    We look for any of the well-known partition column names anywhere in
    the WHERE / ON clauses of the SQL.  This is intentionally fuzzy:
    we err on the side of "yes, there is a filter" so we do not raise
    false alarms when the user clearly remembered to filter.
    """

    found: Set[str] = set()
    candidates = [alias.lower() + "." if alias else "", table.split(".")[-1].lower() + "."]
    candidates.append("")  # also consider unqualified usages

    lower = text.lower()
    for col in PARTITION_COLUMN_NAMES:
        for prefix in candidates:
            # Require the column to appear next to a comparison or
            # function so we do not match field names like "dt_status".
            pattern = re.compile(
                r"\b" + re.escape(prefix) + re.escape(col) + r"\b\s*(=|<|>|in|between|>=|<=)",
                re.IGNORECASE,
            )
            if pattern.search(lower):
                found.add(col)
                break
            # Even a function-wrapped reference counts as a filter,
            # though it loses partition pruning - we will warn about
            # that separately.
            wrapped = re.compile(
                r"[A-Za-z_]+\(\s*" + re.escape(prefix) + re.escape(col) + r"\s*",
                re.IGNORECASE,
            )
            if wrapped.search(lower):
                found.add(col + " (wrapped)")
                break
    return sorted(found)


def extract_tables(parsed: ParsedSQL) -> List[TableReference]:
    """Return every table reference found across all statements and CTEs.

    Tables defined in WITH ... AS (...) blocks are flagged as CTEs so
    later checks can distinguish "physical source" from "intermediate".
    """

    cte_names = {cte.name.lower() for cte in parsed.ctes}
    references: List[TableReference] = []

    # We scan both the CTE bodies and the main query so a missing
    # partition filter inside a CTE is still reported.
    scan_targets: List[str] = [cte.body for cte in parsed.ctes]
    scan_targets.append(parsed.main_query or parsed.stripped)

    seen: Set[str] = set()
    for body in scan_targets:
        if not body:
            continue
        normalized = normalize_whitespace(body)
        for match in _FROM_JOIN_RE.finditer(normalized):
            table_name = match.group(2)
            alias = match.group(3)
            if alias and alias.lower() in {
                "where",
                "on",
                "group",
                "order",
                "limit",
                "join",
                "left",
                "right",
                "inner",
                "outer",
                "full",
                "lateral",
                "cross",
                "semi",
                "anti",
                "having",
                "qualify",
                "distribute",
                "sort",
                "cluster",
                "union",
                "tablesample",
                "using",
            }:
                # The "alias" we caught is actually a SQL keyword -
                # the table had no alias.
                alias = None

            is_cte = table_name.lower() in cte_names
            partition_cols = _find_partition_filters(normalized, alias, table_name)
            has_filter: Optional[bool]
            if is_cte:
                has_filter = None  # Not meaningful for CTEs themselves.
            else:
                has_filter = bool(partition_cols)
            note = ""
            if any("(wrapped)" in col for col in partition_cols):
                note = "Partition column wrapped in a function may disable partition pruning."
            ref = TableReference(
                name=table_name,
                alias=alias,
                is_cte=is_cte,
                has_partition_filter=has_filter,
                partition_filter_columns=partition_cols,
                note=note,
            )
            key = (ref.name.lower(), ref.alias or "", ref.is_cte)
            if key in seen:
                continue
            seen.add(key)
            references.append(ref)

    return references


def extract_tables_from_text(sql: str) -> List[Dict[str, object]]:
    """Convenience wrapper used by scripts/extract_tables.py."""

    parsed = parse(sql)
    return [ref.to_dict() for ref in extract_tables(parsed)]
