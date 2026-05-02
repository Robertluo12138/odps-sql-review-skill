"""parser.py

Lightweight SQL preprocessing utilities.

The goal of this module is *not* to build a perfect SQL AST.  ODPS
SQL has its own dialect (LIFECYCLE, PARTITION, MAPJOIN hints, etc.)
that mainstream parsers do not always cover.  We only need enough
structure to drive static review rules:

* strip comments;
* normalize whitespace (without losing line numbers when reasonable);
* split top-level statements;
* expose CTE blocks and the main query body;
* fall back to regex if ``sqlglot`` is not installed.

Heavy parsing is delegated to ``sqlglot`` only when available, and only
for advisory data.  The regex-based path always runs so we never depend
on the optional package being installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .utils import is_optional_module_available, normalize_whitespace


# Pattern for line comments: -- to end-of-line.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
# Pattern for block comments: /* ... */, possibly multi-line.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Pattern for string literals.  We mask these out before running other
# regex passes so a comment-looking sequence inside a string does not
# confuse us.
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


@dataclass
class CteBlock:
    """A single named subquery extracted from a WITH clause."""

    name: str
    body: str


@dataclass
class ParsedSQL:
    """A normalized representation of a SQL document.

    ``raw`` keeps the original text so reports can show the user the
    exact line they wrote.  ``stripped`` removes comments and is the
    text we run static checks against.
    """

    raw: str
    stripped: str
    statements: List[str] = field(default_factory=list)
    ctes: List[CteBlock] = field(default_factory=list)
    main_query: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "statements": self.statements,
            "ctes": [{"name": cte.name, "body": cte.body} for cte in self.ctes],
            "main_query": self.main_query,
        }


def remove_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments.

    We are careful not to delete characters that look like a comment
    but actually live inside a string literal, e.g. ``'-- not a comment'``.
    To handle that we temporarily replace string literals with
    placeholders, run the comment removal, then restore the literals.
    """

    if not sql:
        return ""

    placeholders: List[str] = []

    def _save(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"__SQL_LIT_{len(placeholders) - 1}__"

    masked = _STRING_LITERAL_RE.sub(_save, sql)
    masked = _BLOCK_COMMENT_RE.sub(" ", masked)
    masked = _LINE_COMMENT_RE.sub("", masked)

    # Restore the literals.
    def _restore(match: "re.Match[str]") -> str:
        index = int(match.group(1))
        return placeholders[index]

    return re.sub(r"__SQL_LIT_(\d+)__", _restore, masked)


def split_statements(sql: str) -> List[str]:
    """Split a SQL document into top-level statements separated by ``;``.

    Semicolons inside string literals must not split the script, so we
    again mask out literals first.
    """

    if not sql:
        return []
    placeholders: List[str] = []

    def _save(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"__SQL_LIT_{len(placeholders) - 1}__"

    masked = _STRING_LITERAL_RE.sub(_save, sql)
    parts = [piece.strip() for piece in masked.split(";")]

    def _restore(text: str) -> str:
        return re.sub(
            r"__SQL_LIT_(\d+)__",
            lambda m: placeholders[int(m.group(1))],
            text,
        )

    return [_restore(p) for p in parts if p]


def extract_ctes(statement: str) -> Tuple[List[CteBlock], str]:
    """Extract WITH ... AS (...) blocks from a single statement.

    Returns the list of CTE blocks (in order) and the remaining query
    body.  The remaining body is the part after the last CTE.

    The implementation walks the WITH clause character by character to
    handle nested parentheses inside CTE bodies correctly.
    """

    if not statement:
        return [], ""

    text = statement.lstrip()
    lower = text.lower()
    if not lower.startswith("with "):
        return [], statement

    ctes: List[CteBlock] = []
    # Skip the leading "with".
    cursor = 4  # length of "with"

    while cursor < len(text):
        # Skip whitespace and an optional comma between CTEs.
        while cursor < len(text) and text[cursor] in " \t\n\r,":
            cursor += 1

        # Capture the CTE name.
        name_match = re.match(r"([A-Za-z_][\w]*)\s*", text[cursor:])
        if not name_match:
            break
        name = name_match.group(1)
        cursor += name_match.end()

        # Optional column list e.g. cte_a (c1, c2)
        if cursor < len(text) and text[cursor] == "(":
            depth = 1
            cursor += 1
            while cursor < len(text) and depth > 0:
                if text[cursor] == "(":
                    depth += 1
                elif text[cursor] == ")":
                    depth -= 1
                cursor += 1

        # Skip whitespace.
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1

        # Expect "AS".
        if text[cursor : cursor + 2].lower() != "as":
            break
        cursor += 2

        # Skip whitespace.
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1

        # Expect the body, parenthesised.
        if cursor >= len(text) or text[cursor] != "(":
            break
        body_start = cursor + 1
        depth = 1
        cursor += 1
        while cursor < len(text) and depth > 0:
            if text[cursor] == "(":
                depth += 1
            elif text[cursor] == ")":
                depth -= 1
            cursor += 1
        body_end = cursor - 1
        body = text[body_start:body_end].strip()
        ctes.append(CteBlock(name=name, body=body))

        # See if a comma follows; if not, we're done with the WITH section.
        rest = text[cursor:].lstrip()
        if not rest.startswith(","):
            break
        # Move cursor past the optional whitespace and comma.
        comma_index = text.find(",", cursor)
        if comma_index < 0:
            break
        cursor = comma_index + 1

    main_query = text[cursor:].strip()
    return ctes, main_query


def parse(sql: str) -> ParsedSQL:
    """Parse a SQL document into a ``ParsedSQL`` value object."""

    raw = sql or ""
    stripped = remove_comments(raw)
    statements = split_statements(stripped)
    ctes: List[CteBlock] = []
    main_query = ""
    if statements:
        # We focus the heavyweight rule checks on the last statement,
        # since INSERT OVERWRITE pipelines often end with the query that
        # actually produces the data.
        for stmt in statements:
            cte_blocks, body = extract_ctes(stmt)
            if cte_blocks:
                ctes.extend(cte_blocks)
                main_query = body
            else:
                main_query = stmt
    return ParsedSQL(
        raw=raw,
        stripped=stripped,
        statements=statements,
        ctes=ctes,
        main_query=main_query,
    )


def try_parse_with_sqlglot(sql: str) -> Optional[object]:
    """Optionally parse with sqlglot for advisory data.

    Returns the sqlglot expression on success or ``None`` if sqlglot is
    not installed or the SQL fails to parse cleanly.  Callers should
    treat ``None`` as "no extra information available" rather than as an
    error.
    """

    if not is_optional_module_available("sqlglot"):
        return None
    try:
        import sqlglot  # type: ignore

        # Try the MaxCompute / Hive dialects since that is closest to ODPS.
        for dialect in ("hive", None):
            try:
                return sqlglot.parse_one(sql, read=dialect)
            except Exception:
                continue
    except Exception:
        return None
    return None


def normalize(sql: str) -> str:
    """Return a normalized representation suitable for regex matching."""

    return normalize_whitespace(remove_comments(sql))
