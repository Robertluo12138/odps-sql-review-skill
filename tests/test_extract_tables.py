"""Tests for table extraction."""

from __future__ import annotations

from odps_sql_review.extract_tables import extract_tables_from_text


def test_extract_simple_from():
    sql = "SELECT * FROM dwd_xxx_order_di WHERE dt = '${bizdate}'"
    tables = extract_tables_from_text(sql)
    assert any(t["name"] == "dwd_xxx_order_di" for t in tables)


def test_extract_alias():
    sql = "SELECT a.id FROM dwd_xxx_order_di a WHERE a.dt = '${bizdate}'"
    tables = extract_tables_from_text(sql)
    table = next(t for t in tables if t["name"] == "dwd_xxx_order_di")
    assert table["alias"] == "a"


def test_partition_filter_detected():
    sql = "SELECT 1 FROM dwd_xxx_order_di WHERE dt = '${bizdate}'"
    tables = extract_tables_from_text(sql)
    table = next(t for t in tables if t["name"] == "dwd_xxx_order_di")
    assert table["has_partition_filter"] is True
    assert "dt" in table["partition_filter_columns"]


def test_partition_filter_missing():
    sql = "SELECT user_id FROM dwd_xxx_order_di GROUP BY user_id"
    tables = extract_tables_from_text(sql)
    table = next(t for t in tables if t["name"] == "dwd_xxx_order_di")
    assert table["has_partition_filter"] is False


def test_partition_wrapped_in_function_flagged():
    sql = "SELECT 1 FROM dwd_xxx_order_di WHERE substr(dt, 1, 6) = '202504'"
    tables = extract_tables_from_text(sql)
    table = next(t for t in tables if t["name"] == "dwd_xxx_order_di")
    # Should still be flagged as having a filter, but the note should warn.
    assert table["has_partition_filter"] is True
    assert "(wrapped)" in " ".join(table["partition_filter_columns"])


def test_join_table_extracted():
    sql = (
        "SELECT 1 FROM a a JOIN b b ON a.id = b.id "
        "WHERE a.dt = '${bizdate}' AND b.dt = '${bizdate}'"
    )
    tables = extract_tables_from_text(sql)
    names = {t["name"] for t in tables}
    assert "a" in names and "b" in names


def test_cte_marked_as_cte():
    sql = (
        "WITH cte_a AS (SELECT user_id FROM dwd_xxx_order_di WHERE dt = '${bizdate}') "
        "SELECT * FROM cte_a"
    )
    tables = extract_tables_from_text(sql)
    cte_ref = next(t for t in tables if t["name"] == "cte_a")
    assert cte_ref["is_cte"] is True
