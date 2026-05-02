"""Tests for JOIN extraction and risk detection."""

from __future__ import annotations

from odps_sql_review.extract_joins import extract_joins_from_text


def test_inner_join_no_risks():
    sql = (
        "SELECT 1 FROM a "
        "JOIN b ON a.id = b.id "
        "WHERE a.dt = '${bizdate}'"
    )
    joins = extract_joins_from_text(sql)
    assert len(joins) == 1
    assert "JOIN" in joins[0]["join_type"]
    assert joins[0]["join_keys"] == [["a.id", "b.id"]]


def test_left_join_invalidated_by_where():
    sql = (
        "SELECT 1 FROM a "
        "LEFT JOIN b ON a.id = b.id "
        "WHERE b.status = 1"
    )
    joins = extract_joins_from_text(sql)
    assert any("退化为 INNER JOIN" in r for r in joins[0]["risks"])


def test_left_join_with_is_null_not_invalidated():
    sql = (
        "SELECT 1 FROM a "
        "LEFT JOIN b ON a.id = b.id "
        "WHERE b.status IS NULL"
    )
    joins = extract_joins_from_text(sql)
    assert not any("退化为 INNER JOIN" in r for r in joins[0]["risks"])


def test_on_1_equals_1_flagged():
    sql = "SELECT 1 FROM a JOIN b ON 1 = 1"
    joins = extract_joins_from_text(sql)
    assert any("ON 1=1" in r or "笛卡尔积" in r for r in joins[0]["risks"])


def test_or_in_on_flagged():
    sql = (
        "SELECT 1 FROM a "
        "JOIN b ON a.id = b.id OR a.alt_id = b.id"
    )
    joins = extract_joins_from_text(sql)
    assert any("OR" in r for r in joins[0]["risks"])


def test_cast_in_on_flagged():
    sql = (
        "SELECT 1 FROM a "
        "JOIN b ON CAST(a.id AS STRING) = b.id"
    )
    joins = extract_joins_from_text(sql)
    assert any("CAST" in r for r in joins[0]["risks"])
