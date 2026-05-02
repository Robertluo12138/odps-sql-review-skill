"""utils.py

Small helper functions shared across the package. We try to keep this
module dependency free so it can be imported safely from anywhere.

The user reading this code is still learning Python, so the functions
are written in a straightforward style and commented heavily.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional


# Common ODPS partition column names.  These are the patterns we look
# for when checking whether a SQL statement filters on a partition.
PARTITION_COLUMN_NAMES = (
    "dt",
    "ds",
    "pt",
    "hh",
    "bizdate",
    "biz_date",
    "biz_dt",
    "stat_date",
    "stat_dt",
    "log_date",
    "log_dt",
    "month_id",
    "year_id",
)


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace down to a single space.

    This is useful when running regex matches because real SQL uses
    inconsistent spacing, tabs and newlines.
    """

    if text is None:
        return ""
    # Replace any whitespace run (spaces, tabs, newlines) with a single space.
    return re.sub(r"\s+", " ", text).strip()


def safe_lower(text: Optional[str]) -> str:
    """Lower-case a string, returning empty string for None."""

    if text is None:
        return ""
    return text.lower()


def deduplicate(items: Iterable) -> List:
    """Return items in original order with duplicates removed.

    We use this so reports do not list the same finding ten times when
    the underlying SQL repeats a problematic pattern.
    """

    seen = set()
    result = []
    for item in items:
        # Items can be unhashable (e.g. dicts) so guard with a try.
        try:
            key = item
            if key in seen:
                continue
            seen.add(key)
        except TypeError:
            # Fall back to string repr for unhashable structures.
            key = repr(item)
            if key in seen:
                continue
            seen.add(key)
        result.append(item)
    return result


def looks_like_partition_column(name: str) -> bool:
    """Return True when a column name matches a common partition pattern."""

    if not name:
        return False
    name = name.strip().lower()
    if name in PARTITION_COLUMN_NAMES:
        return True
    # Some teams use prefixed forms like "a.dt" or "t1.bizdate".
    if "." in name:
        tail = name.rsplit(".", 1)[-1]
        if tail in PARTITION_COLUMN_NAMES:
            return True
    return False


def truncate(text: str, length: int = 240) -> str:
    """Return text shortened to ``length`` characters with an ellipsis."""

    if text is None:
        return ""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[: length - 3].rstrip() + "..."


def is_optional_module_available(module_name: str) -> bool:
    """Best-effort check whether an optional dependency can be imported.

    We avoid actually importing here so callers can decide themselves
    whether to take the slow import cost.
    """

    import importlib.util

    return importlib.util.find_spec(module_name) is not None
