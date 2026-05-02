"""static_check.py

Top-level orchestration for the static review.

The review pipeline is intentionally linear so it is easy to follow:

1. Parse the SQL document into a ``ParsedSQL`` value object.
2. Extract source tables, joins, and metrics.
3. Optionally enrich the table list with table profile information.
4. Run all rules from ``rules.py`` to produce ``Finding`` objects.
5. Return a structured ``ReviewResult`` ready for serialization.

Nothing in here connects to ODPS or executes SQL.  Even when a table
profile is provided we only read the YAML file - we never touch the
underlying database.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .extract_joins import JoinClause, extract_joins
from .extract_metrics import Metric, extract_metrics
from .extract_tables import TableReference, extract_tables
from .parser import ParsedSQL, parse
from .rules import Finding, run_all_rules
from .utils import is_optional_module_available


@dataclass
class TableProfile:
    """Metadata about a single table.  All fields are optional."""

    partition_cols: List[str] = field(default_factory=list)
    grain: List[str] = field(default_factory=list)
    unique_keys: List[List[str]] = field(default_factory=list)
    size_level: Optional[str] = None
    table_type: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ReviewResult:
    """The full output of a static review run."""

    parsed: ParsedSQL
    tables: List[TableReference]
    joins: List[JoinClause]
    metrics: List[Metric]
    findings: List[Finding]
    table_profile_path: Optional[str] = None
    overall_risk: str = "low"
    blocking: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_risk": self.overall_risk,
            "blocking": self.blocking,
            "table_profile_path": self.table_profile_path,
            "tables": [t.to_dict() for t in self.tables],
            "joins": [j.to_dict() for j in self.joins],
            "metrics": [m.to_dict() for m in self.metrics],
            "findings": [f.to_dict() for f in self.findings],
            "parsed": self.parsed.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# --------------------------------------------------------------------- #
# Table profile loading                                                 #
# --------------------------------------------------------------------- #


def load_table_profile(path: str) -> Dict[str, TableProfile]:
    """Load a YAML table profile if PyYAML is available.

    The YAML schema is:

    ```yaml
    tables:
      dwd_xxx:
        partition_cols: ["dt"]
        grain: ["dt", "user_id"]
        unique_keys:
          - ["dt", "user_id"]
        size_level: "large"
        table_type: "fact"
        description: "..."
    ```

    If PyYAML is not installed we print a warning to stderr and return
    an empty profile rather than crashing.  This keeps the tool usable
    on minimal Python environments.
    """

    if not path:
        return {}
    if not os.path.exists(path):
        print(f"[odps-sql-review] table profile not found: {path}")
        return {}

    if not is_optional_module_available("yaml"):
        print(
            "[odps-sql-review] PyYAML is not installed; "
            "skipping table profile parsing. "
            "Install with `pip install pyyaml` to enable richer reviews."
        )
        return {}

    try:
        import yaml  # type: ignore
    except Exception as exc:
        print(f"[odps-sql-review] failed to import yaml: {exc}")
        return {}

    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except Exception as exc:
            print(f"[odps-sql-review] failed to parse YAML: {exc}")
            return {}

    profiles: Dict[str, TableProfile] = {}
    tables_section = data.get("tables", {}) if isinstance(data, dict) else {}
    for name, entry in (tables_section or {}).items():
        if not isinstance(entry, dict):
            continue
        profiles[str(name).lower()] = TableProfile(
            partition_cols=list(entry.get("partition_cols", []) or []),
            grain=list(entry.get("grain", []) or []),
            unique_keys=[list(k) for k in (entry.get("unique_keys") or [])],
            size_level=entry.get("size_level"),
            table_type=entry.get("table_type"),
            description=entry.get("description"),
        )
    return profiles


def _enrich_tables_with_profile(
    tables: List[TableReference], profiles: Dict[str, TableProfile]
) -> None:
    """Update table flags using a table profile when available."""

    for table in tables:
        key = table.name.lower()
        # Allow lookups by short name (without project / schema prefix).
        short_key = key.rsplit(".", 1)[-1]
        profile = profiles.get(key) or profiles.get(short_key)
        if not profile:
            continue
        if profile.partition_cols and not table.has_partition_filter:
            # Profile says the table is partitioned but we did not see a filter.
            table.note = (
                table.note
                + (" " if table.note else "")
                + f"profile 中声明分区列 {profile.partition_cols}，请确认是否过滤。"
            )
        if profile.size_level == "small":
            table.note = (
                table.note
                + (" " if table.note else "")
                + "profile 标记为 small，可考虑 MAPJOIN。"
            )


# --------------------------------------------------------------------- #
# Risk aggregation                                                      #
# --------------------------------------------------------------------- #


def _aggregate_risk(findings: List[Finding]) -> tuple[str, bool]:
    """Pick the overall risk level from a list of findings."""

    if any(f.risk_level == "high" for f in findings):
        return "high", any(f.blocking for f in findings)
    if any(f.risk_level == "medium" for f in findings):
        return "medium", any(f.blocking for f in findings)
    return "low", False


# --------------------------------------------------------------------- #
# Public entry point                                                    #
# --------------------------------------------------------------------- #


def run_static_check(
    sql: str,
    table_profile_path: Optional[str] = None,
    focus: str = "all",
) -> ReviewResult:
    """Run the full static review pipeline on a SQL document.

    Parameters
    ----------
    sql:
        Raw SQL text.
    table_profile_path:
        Optional path to a YAML file describing tables.
    focus:
        ``"correctness"`` keeps only findings in correctness categories
        (partition / join / metric / insert / date).
        ``"performance"`` keeps only performance findings.
        ``"all"`` keeps everything.
    """

    parsed = parse(sql)
    tables = extract_tables(parsed)
    joins = extract_joins(parsed)
    metrics = extract_metrics(parsed)

    profiles = load_table_profile(table_profile_path) if table_profile_path else {}
    _enrich_tables_with_profile(tables, profiles)

    findings = run_all_rules(parsed, tables, joins, metrics)

    if focus == "correctness":
        keep = {
            "partition_pruning",
            "join_safety",
            "metric_definition",
            "insert_overwrite",
            "date_boundary",
        }
        findings = [f for f in findings if f.category in keep]
    elif focus == "performance":
        findings = [f for f in findings if f.category == "performance"]

    overall, blocking = _aggregate_risk(findings)
    return ReviewResult(
        parsed=parsed,
        tables=tables,
        joins=joins,
        metrics=metrics,
        findings=findings,
        table_profile_path=table_profile_path,
        overall_risk=overall,
        blocking=blocking,
    )
