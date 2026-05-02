"""extract_metrics.py

Extract aggregate metric expressions from a SQL statement.

For every metric we try to identify:

* the original expression text;
* the alias (if any);
* the aggregation type (SUM / COUNT / COUNT DISTINCT / AVG / MIN / MAX
  / RATIO / CASE WHEN);
* a possible numerator and denominator for ratio metrics;
* any inline filters from CASE WHEN expressions;
* whether the metric needs business-side confirmation.

These hints feed directly into the "指标口径解释" section of the
review report.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import ParsedSQL, parse
from .utils import normalize_whitespace, truncate


_AGG_FUNCS = (
    "sum",
    "count",
    "avg",
    "min",
    "max",
    "approx_distinct",
    "approx_count_distinct",
    "collect_set",
    "collect_list",
    "percentile",
    "percentile_approx",
)
_AGG_RE = re.compile(
    r"\b(" + "|".join(_AGG_FUNCS) + r")\s*\(",
    re.IGNORECASE,
)


@dataclass
class Metric:
    """A single metric expression discovered in the SELECT list."""

    metric_expr: str
    metric_alias: Optional[str]
    aggregate_type: str
    possible_numerator: Optional[str] = None
    possible_denominator: Optional[str] = None
    filters_in_case_when: List[str] = field(default_factory=list)
    need_business_confirm: bool = True
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _find_balanced(text: str, start: int) -> int:
    """Return the index of the matching ')' for the '(' at ``start - 1``."""

    depth = 1
    cursor = start
    while cursor < len(text) and depth > 0:
        ch = text[cursor]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        cursor += 1
    return cursor  # one past the closing paren


def _extract_select_list(query: str) -> Optional[str]:
    """Return the substring between the top-level SELECT and FROM.

    The input is normalized to single spaces first so multi-line SQL
    parses the same way as single-line SQL.  Comments must already be
    removed by the caller.
    """

    if not query:
        return None
    # Collapse all whitespace runs so "SELECT\n    user_id\nFROM" becomes
    # "SELECT user_id FROM" and our character-by-character scan can rely
    # on single spaces.
    query = normalize_whitespace(query)
    lower = query.lower()
    if not lower.startswith("select"):
        # The query may start with "select distinct" etc. Search for the
        # first SELECT keyword.
        match = re.search(r"\bselect\b", lower)
        if not match:
            return None
        select_start = match.end()
    else:
        select_start = len("select")
    # Skip optional DISTINCT / ALL.
    rest = query[select_start:].lstrip()
    rest_lower = rest.lower()
    if rest_lower.startswith("distinct"):
        select_start = lower.find("distinct", select_start) + len("distinct")
    elif rest_lower.startswith("all "):
        select_start = lower.find("all ", select_start) + len("all ")

    # Find FROM at depth 0.
    depth = 0
    cursor = select_start
    while cursor < len(query):
        ch = query[cursor]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and query[cursor : cursor + 6].lower() == " from ":
            return query[select_start:cursor].strip()
        cursor += 1
    # No FROM (could be SELECT 1 + 1).
    return query[select_start:].strip()


def _split_select_columns(select_list: str) -> List[str]:
    """Split a SELECT list on commas at depth 0."""

    columns: List[str] = []
    depth = 0
    buffer: List[str] = []
    for ch in select_list:
        if ch == "(":
            depth += 1
            buffer.append(ch)
        elif ch == ")":
            depth -= 1
            buffer.append(ch)
        elif ch == "," and depth == 0:
            columns.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(ch)
    if buffer:
        columns.append("".join(buffer).strip())
    return [col for col in columns if col]


def _extract_alias(column: str) -> Tuple[str, Optional[str]]:
    """Split ``expr AS alias`` (or ``expr alias``) into (expr, alias)."""

    if not column:
        return column, None
    # ``expr AS alias`` - case insensitive.
    match = re.search(r"\bas\s+([A-Za-z_][\w]*)\s*$", column, re.IGNORECASE)
    if match:
        expr = column[: match.start()].strip()
        return expr, match.group(1)
    # ``expr alias`` without AS, only when alias is a single identifier
    # at the very end and the previous character is not part of an
    # operator / paren.
    match = re.search(r"([\)\w])\s+([A-Za-z_][\w]*)\s*$", column)
    if match and match.group(2).lower() not in {"asc", "desc", "nulls"}:
        # Make sure the rightmost token is not actually a keyword inside
        # a function (e.g. SUM(x DESC) - which is invalid anyway).
        alias = match.group(2)
        expr = column[: match.start(2)].rstrip()
        # Heuristic: ensure the candidate alias does not look like a
        # built-in function call missing parens (rare).
        return expr, alias
    return column, None


def _classify(expr: str) -> Tuple[str, Optional[str], Optional[str], List[str]]:
    """Classify an expression into an aggregate type and extract hints.

    Returns (aggregate_type, possible_numerator, possible_denominator,
    case_when_filters).
    """

    text = expr.strip()
    lower = text.lower()
    case_filters: List[str] = []

    # Detect ratio expressions:  a / b   when both sides reference an
    # aggregate or numeric expression.  We only flag the simplest forms.
    slash_match = re.match(r"^(.*?)\s*/\s*(.*)$", text)
    if slash_match and slash_match.group(2) and slash_match.group(1):
        left, right = slash_match.group(1), slash_match.group(2)
        if any(func in left.lower() for func in _AGG_FUNCS) or any(
            func in right.lower() for func in _AGG_FUNCS
        ):
            return "RATIO", left.strip(), right.strip(), case_filters

    # Capture CASE WHEN ... THEN ... END filters as inline filters.
    for match in re.finditer(r"\bcase\b(.*?)\bend\b", text, re.IGNORECASE | re.DOTALL):
        case_body = match.group(1)
        when_clauses = re.findall(r"\bwhen\b\s*(.*?)\s*\bthen\b", case_body, re.IGNORECASE | re.DOTALL)
        for clause in when_clauses:
            case_filters.append(normalize_whitespace(clause))

    # COUNT DISTINCT
    if re.match(r"^\s*count\s*\(\s*distinct\b", lower):
        return "COUNT_DISTINCT", None, None, case_filters
    if re.match(r"^\s*approx_distinct\b", lower) or re.match(
        r"^\s*approx_count_distinct\b", lower
    ):
        return "APPROX_DISTINCT", None, None, case_filters
    if re.match(r"^\s*count\s*\(", lower):
        return "COUNT", None, None, case_filters
    if re.match(r"^\s*sum\s*\(", lower):
        return "SUM", None, None, case_filters
    if re.match(r"^\s*avg\s*\(", lower):
        return "AVG", None, None, case_filters
    if re.match(r"^\s*min\s*\(", lower):
        return "MIN", None, None, case_filters
    if re.match(r"^\s*max\s*\(", lower):
        return "MAX", None, None, case_filters
    if re.match(r"^\s*case\b", lower):
        return "CASE_WHEN", None, None, case_filters
    if any(func in lower for func in _AGG_FUNCS):
        return "OTHER_AGG", None, None, case_filters
    return "PASS_THROUGH", None, None, case_filters


def extract_metrics(parsed: ParsedSQL) -> List[Metric]:
    """Extract metric expressions from the main SELECT list."""

    main_query = parsed.main_query or parsed.stripped
    select_list = _extract_select_list(main_query)
    if select_list is None:
        return []

    metrics: List[Metric] = []
    for column in _split_select_columns(select_list):
        expr, alias = _extract_alias(column)
        agg_type, numerator, denominator, case_filters = _classify(expr)
        if agg_type == "PASS_THROUGH" and not case_filters:
            # Skip pure dimension columns - they are not metrics.
            continue
        metric = Metric(
            metric_expr=truncate(expr, 240),
            metric_alias=alias,
            aggregate_type=agg_type,
            possible_numerator=truncate(numerator, 120) if numerator else None,
            possible_denominator=truncate(denominator, 120) if denominator else None,
            filters_in_case_when=[truncate(f, 160) for f in case_filters],
        )
        # Heuristic note for popular long-period distinct user metrics.
        alias_lower = (alias or "").lower()
        if agg_type == "COUNT_DISTINCT" and any(
            keyword in alias_lower for keyword in ("mau", "yau", "wau", "active_user", "uv")
        ):
            metric.note = (
                "看起来是长周期去重指标，建议结合用户日活中间表（dt + user_id）做计算，"
                "避免在原始明细表上对长周期分区直接 COUNT(DISTINCT)。"
            )
        metrics.append(metric)
    return metrics


def extract_metrics_from_text(sql: str) -> List[Dict[str, object]]:
    """Convenience wrapper used by scripts/extract_metrics.py."""

    parsed = parse(sql)
    return [metric.to_dict() for metric in extract_metrics(parsed)]
