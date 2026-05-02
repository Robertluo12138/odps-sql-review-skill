"""extract_joins.py

Extract JOIN clauses, their kind, ON conditions, and structural risks.

This module exists to power the high-risk JOIN review rules:

* LEFT JOIN invalidated by WHERE filtering on the right-side alias;
* JOIN ON 1 = 1 / missing ON / OR in ON;
* possible row explosion due to non-unique right-side keys;
* type mismatches (CAST in ON);
* MAPJOIN / DISTMAPJOIN / SKEWJOIN candidates.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import ParsedSQL, parse
from .utils import normalize_whitespace


# Capture a JOIN clause and the ON condition that follows it.  We use a
# manual cursor walk inside ``_split_join_blocks`` because ON conditions
# may contain nested parentheses.
_JOIN_KEYWORD_RE = re.compile(
    r"\b((?:left|right|full|inner|cross|semi|anti|left\s+semi|left\s+anti|left\s+outer|right\s+outer|full\s+outer)?\s*join)\b",
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(
    r"^\s*(?:\(\s*)?([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*){0,2}|\([^()]*\))\s*(?:as\s+)?([A-Za-z_][\w]*)?",
    re.IGNORECASE,
)


@dataclass
class JoinClause:
    """A single JOIN ... ON ... clause.

    ``risks`` collects human-readable Chinese explanations so the report
    layer does not need to reformulate them.
    """

    join_type: str
    right_table: str
    right_alias: Optional[str]
    on_condition: str
    join_keys: List[Tuple[str, str]] = field(default_factory=list)
    raw_clause: str = ""
    risks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "join_type": self.join_type,
            "right_table": self.right_table,
            "right_alias": self.right_alias,
            "on_condition": self.on_condition,
            # Convert tuples to plain lists so the output is JSON-friendly
            # and stable for assertions like ``[[left, right]]``.
            "join_keys": [list(pair) for pair in self.join_keys],
            "raw_clause": self.raw_clause,
            "risks": list(self.risks),
        }


def _split_join_blocks(text: str) -> List[Tuple[str, int, int]]:
    """Find all JOIN keyword positions in ``text``.

    Returns a list of (matched_keyword, start, end) tuples where
    ``start`` and ``end`` are character offsets into ``text``.
    """

    return [
        (match.group(1), match.start(), match.end())
        for match in _JOIN_KEYWORD_RE.finditer(text)
    ]


def _find_balanced_on_clause(text: str, start: int) -> Tuple[str, int]:
    """Starting from ``start``, return the ON clause body and its end index.

    The end index points past the ON condition, ready for the next
    keyword to be parsed.
    """

    # Skip whitespace.
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1

    if text[cursor : cursor + 2].lower() != "on":
        return "", cursor

    cursor += 2  # past "on"
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1

    body_start = cursor
    depth = 0
    # The condition continues until the next top-level WHERE / GROUP /
    # ORDER / LIMIT / JOIN / UNION keyword, or end of statement.
    stop_keywords = (
        "where ",
        "group by ",
        "order by ",
        "having ",
        "qualify ",
        "limit ",
        "union ",
        "cluster by ",
        "distribute by ",
        "lateral view ",
        "left join ",
        "right join ",
        "inner join ",
        "full join ",
        "cross join ",
        "semi join ",
        "anti join ",
        "join ",
    )
    while cursor < len(text):
        ch = text[cursor]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            lower_tail = text[cursor:].lower()
            if any(lower_tail.startswith(k) for k in stop_keywords):
                break
        cursor += 1
    return text[body_start:cursor].strip(), cursor


def _extract_join_keys(on_text: str) -> List[Tuple[str, str]]:
    """Parse simple equality conditions ``a.x = b.y``.

    More complex conditions are not split; the rule layer still inspects
    the raw ON text for OR / 1=1 / etc.
    """

    keys: List[Tuple[str, str]] = []
    if not on_text:
        return keys
    # Split on AND, but only at the top level - we approximate that by
    # ignoring AND inside parentheses.
    parts: List[str] = []
    depth = 0
    buffer: List[str] = []
    tokens = re.split(r"(\(|\)|\band\b)", on_text, flags=re.IGNORECASE)
    for token in tokens:
        if not token:
            continue
        if token == "(":
            depth += 1
            buffer.append(token)
        elif token == ")":
            depth -= 1
            buffer.append(token)
        elif token.lower() == "and" and depth == 0:
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(token)
    if buffer:
        parts.append("".join(buffer).strip())

    for part in parts:
        equality = re.match(
            r"\s*([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\s*=\s*([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\s*$",
            part,
        )
        if equality:
            keys.append((equality.group(1), equality.group(2)))
    return keys


def _detect_left_join_invalidation(
    join_clause: JoinClause, where_text: str
) -> Optional[str]:
    """Return a warning if a LEFT/RIGHT JOIN is invalidated by the WHERE.

    A common production bug: ``LEFT JOIN b ... WHERE b.status = 1`` filters
    out the unmatched-left rows so the LEFT JOIN behaves as INNER JOIN.
    """

    join_type_lower = join_clause.join_type.lower()
    if "left" not in join_type_lower and "right" not in join_type_lower:
        return None
    if not where_text:
        return None

    alias = join_clause.right_alias
    target_aliases = []
    if alias:
        target_aliases.append(alias.lower())
    # Also consider the bare table name as alias.
    target_aliases.append(join_clause.right_table.split(".")[-1].lower())

    where_lower = where_text.lower()
    for alias_name in target_aliases:
        if not alias_name:
            continue
        # Look for `alias.column` references but allow IS NULL checks
        # which intentionally preserve LEFT JOIN semantics.
        pattern = re.compile(
            r"\b" + re.escape(alias_name) + r"\.[A-Za-z_][\w]*\b\s*(?!is\s+null)([=<>!]|in\b|like\b|between\b)",
            re.IGNORECASE,
        )
        if pattern.search(where_lower):
            return (
                f"LEFT/RIGHT JOIN 的右表别名 `{alias_name}` 被 WHERE 直接过滤，"
                "可能让外连接退化为 INNER JOIN，请将条件移到 ON 子句或子查询里。"
            )
    return None


def _extract_where_clause(text: str) -> str:
    """Return the WHERE body text (without the leading WHERE)."""

    lower = text.lower()
    where_index = lower.rfind(" where ")
    if where_index < 0:
        # Could be at the start of the buffer.
        if lower.startswith("where "):
            where_index = 0
        else:
            return ""
    cursor = where_index + len(" where ")
    if where_index == 0:
        cursor = len("where ")
    end_keywords = (
        " group by ",
        " order by ",
        " having ",
        " qualify ",
        " limit ",
        " union ",
        " cluster by ",
        " distribute by ",
    )
    end = len(text)
    for keyword in end_keywords:
        idx = lower.find(keyword, cursor)
        if idx >= 0 and idx < end:
            end = idx
    return text[cursor:end].strip()


def extract_joins(parsed: ParsedSQL) -> List[JoinClause]:
    """Extract all JOIN clauses from CTE bodies and the main query."""

    joins: List[JoinClause] = []
    bodies: List[str] = [cte.body for cte in parsed.ctes]
    bodies.append(parsed.main_query or parsed.stripped)

    for body in bodies:
        if not body:
            continue
        normalized = normalize_whitespace(body)
        where_clause = _extract_where_clause(normalized)
        for keyword, _start, end in _split_join_blocks(normalized):
            tail = normalized[end:]
            table_match = _TABLE_REF_RE.match(tail)
            if not table_match:
                continue
            right_table = table_match.group(1)
            right_alias = table_match.group(2)
            end_pos = table_match.end()
            if right_alias and right_alias.lower() in {
                "on",
                "where",
                "group",
                "order",
                "having",
                "limit",
                "left",
                "right",
                "inner",
                "outer",
                "full",
                "cross",
                "semi",
                "anti",
                "join",
                "qualify",
                "lateral",
                "tablesample",
                "using",
                "union",
            }:
                # The regex captured a SQL keyword as if it were an alias.
                # Rewind ``end_pos`` so the ON clause parsing can still see
                # the keyword.
                end_pos = table_match.start(2)
                right_alias = None

            after_table = tail[end_pos:]
            on_body, _ = _find_balanced_on_clause(after_table, 0)
            join_clause = JoinClause(
                join_type=normalize_whitespace(keyword).upper(),
                right_table=right_table,
                right_alias=right_alias,
                on_condition=on_body,
                join_keys=_extract_join_keys(on_body),
                raw_clause=f"{keyword.upper()} {right_table}"
                + (f" {right_alias}" if right_alias else "")
                + (f" ON {on_body}" if on_body else ""),
            )
            # Risk detection.
            if not on_body and "cross" not in join_clause.join_type.lower():
                join_clause.risks.append(
                    "JOIN 缺少 ON 条件，会触发笛卡尔积，必须立即修复。"
                )
            elif re.search(r"\bon\s+1\s*=\s*1\b", "on " + on_body, re.IGNORECASE):
                join_clause.risks.append(
                    "JOIN 使用 `ON 1=1`，等价于笛卡尔积，请补充真实关联键。"
                )
            if re.search(r"\bor\b", on_body, re.IGNORECASE):
                join_clause.risks.append(
                    "JOIN 的 ON 条件中包含 OR，可能导致行数膨胀，建议拆分为 UNION 或重新设计关联逻辑。"
                )
            if re.search(r"\bcast\s*\(", on_body, re.IGNORECASE):
                join_clause.risks.append(
                    "JOIN 的 ON 条件包含 CAST，左右两边类型不一致，需要核实数据是否会被静默过滤。"
                )
            if not join_clause.join_keys and on_body and "1=1" not in on_body.replace(" ", ""):
                join_clause.risks.append(
                    "未识别到清晰的等值关联键，请确认 JOIN 是否为非等值关联或存在表达式关联。"
                )
            invalidation = _detect_left_join_invalidation(join_clause, where_clause)
            if invalidation:
                join_clause.risks.append(invalidation)
            joins.append(join_clause)
    return joins


def extract_joins_from_text(sql: str) -> List[Dict[str, object]]:
    """Convenience wrapper used by scripts/extract_joins.py."""

    parsed = parse(sql)
    return [join.to_dict() for join in extract_joins(parsed)]
