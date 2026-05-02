"""Tests for metric extraction."""

from __future__ import annotations

from odps_sql_review.extract_metrics import extract_metrics_from_text


def test_sum_metric_extracted():
    sql = "SELECT user_id, SUM(amt) AS total_amt FROM t GROUP BY user_id"
    metrics = extract_metrics_from_text(sql)
    aliases = [m["metric_alias"] for m in metrics]
    assert "total_amt" in aliases
    sum_metric = next(m for m in metrics if m["metric_alias"] == "total_amt")
    assert sum_metric["aggregate_type"] == "SUM"


def test_count_distinct_extracted():
    sql = "SELECT COUNT(DISTINCT user_id) AS uv FROM t WHERE dt='2024-01-01'"
    metrics = extract_metrics_from_text(sql)
    metric = next(m for m in metrics if m["metric_alias"] == "uv")
    assert metric["aggregate_type"] == "COUNT_DISTINCT"


def test_long_period_distinct_user_metric_has_note():
    sql = (
        "SELECT COUNT(DISTINCT user_id) AS mau "
        "FROM dwd_xxx_user_action_di "
        "WHERE dt BETWEEN '20240101' AND '20240131'"
    )
    metrics = extract_metrics_from_text(sql)
    metric = next(m for m in metrics if m["metric_alias"] == "mau")
    assert metric["note"], "Should add a note for MAU-style metric"


def test_ratio_metric_detected():
    sql = "SELECT SUM(a) / SUM(b) AS ratio FROM t WHERE dt='2024-01-01'"
    metrics = extract_metrics_from_text(sql)
    metric = next(m for m in metrics if m["metric_alias"] == "ratio")
    assert metric["aggregate_type"] == "RATIO"
    assert metric["possible_numerator"] is not None
    assert metric["possible_denominator"] is not None


def test_case_when_filters_collected():
    sql = (
        "SELECT COUNT(DISTINCT CASE WHEN action = 'pay' THEN user_id END) AS pay_uv "
        "FROM t WHERE dt='2024-01-01'"
    )
    metrics = extract_metrics_from_text(sql)
    metric = next(m for m in metrics if m["metric_alias"] == "pay_uv")
    assert any("action" in f for f in metric["filters_in_case_when"])


def test_dimension_columns_skipped():
    sql = "SELECT user_id, SUM(amt) AS total FROM t GROUP BY user_id"
    metrics = extract_metrics_from_text(sql)
    aliases = [m["metric_alias"] for m in metrics]
    assert "user_id" not in aliases  # dimension column should not be a metric
