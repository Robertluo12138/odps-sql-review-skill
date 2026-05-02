"""Tests for the orchestrating static_check pipeline."""

from __future__ import annotations

import json
import os

from odps_sql_review.report import render_json, render_markdown
from odps_sql_review.static_check import run_static_check


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The pure skill package lives under ``skill_package/odps-sql-review`` and
# is the single source of truth for example SQL files.
_EXAMPLES = os.path.join(_REPO_ROOT, "skill_package", "odps-sql-review", "examples")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_left_join_invalidation_flagged_as_high():
    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    result = run_static_check(sql)
    assert result.overall_risk == "high"
    assert result.blocking is True
    rule_ids = {f.rule_id for f in result.findings}
    assert "J005" in rule_ids


def test_no_partition_flagged_as_high():
    sql = _read(os.path.join(_EXAMPLES, "bad_no_partition.sql"))
    result = run_static_check(sql)
    assert result.overall_risk == "high"
    rule_ids = {f.rule_id for f in result.findings}
    assert "P001" in rule_ids


def test_count_distinct_long_window_flagged():
    sql = _read(os.path.join(_EXAMPLES, "bad_count_distinct.sql"))
    result = run_static_check(sql)
    assert result.overall_risk == "high"
    rule_ids = {f.rule_id for f in result.findings}
    assert "PF006" in rule_ids


def test_dynamic_partition_flagged():
    sql = _read(os.path.join(_EXAMPLES, "bad_dynamic_partition.sql"))
    result = run_static_check(sql)
    rule_ids = {f.rule_id for f in result.findings}
    assert "I001" in rule_ids


def test_join_explosion_flagged_as_medium():
    sql = _read(os.path.join(_EXAMPLES, "bad_join_explosion.sql"))
    result = run_static_check(sql)
    rule_ids = {f.rule_id for f in result.findings}
    assert "J010" in rule_ids


def test_markdown_report_has_required_sections():
    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    result = run_static_check(sql)
    md = render_markdown(result)
    for section in (
        "## 0. 总体结论",
        "## 1. 高风险问题",
        "## 2. 中风险问题",
        "## 3. 低风险 / 可读性问题",
        "## 4. 性能优化建议",
        "## 5. 指标口径解释",
        "## 6. 建议改写 SQL",
        "## 7. 上线前验数 SQL",
        "## 8. 需要我补充的信息",
    ):
        assert section in md, f"missing section: {section}"


def test_json_report_serialisable():
    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    result = run_static_check(sql)
    payload = json.loads(render_json(result))
    assert payload["overall_risk"] == "high"
    assert "validation_sql" in payload


def test_focus_correctness_filters_out_performance():
    sql = _read(os.path.join(_EXAMPLES, "bad_count_distinct.sql"))
    result = run_static_check(sql, focus="correctness")
    categories = {f.category for f in result.findings}
    assert "performance" not in categories


def test_focus_performance_keeps_only_performance():
    sql = _read(os.path.join(_EXAMPLES, "bad_count_distinct.sql"))
    result = run_static_check(sql, focus="performance")
    categories = {f.category for f in result.findings}
    assert categories.issubset({"performance"})
