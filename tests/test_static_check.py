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


# --------------------------------------------------------------------- #
# Zero-profile contract regressions                                     #
# --------------------------------------------------------------------- #


def _section(md: str, header: str, next_header: str) -> str:
    """Return the body of ``header`` up to ``next_header`` (markdown helper)."""

    return md.split(header, 1)[1].split(next_header, 1)[0]


_PLAIN_AGG_SQL = """
INSERT OVERWRITE TABLE ads_xxx_user_order_di PARTITION (dt = '${bizdate}')
SELECT o.user_id,
       SUM(o.order_amt) AS gmv,
       COUNT(1)         AS order_cnt
FROM   dwd_xxx_order_di o
JOIN   dim_xxx_shop_df s ON o.shop_id = s.shop_id
WHERE  o.dt = '${bizdate}'
  AND  s.dt = '${bizdate}'
GROUP BY o.user_id;
"""


def test_zero_profile_section_8_does_not_demand_full_table_dictionary():
    """Section 8 must not list every physical table's full metadata as a
    fixed checklist (zero-profile minimum-information principle)."""

    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    md = render_markdown(run_static_check(sql))
    section_8 = _section(md, "## 8. 需要我补充的信息", "\n>")

    # Old fixed checklist phrasing: "<table> 的分区列、表粒度、唯一键、表大小级别".
    # Each of these substrings on its own is fine; the *combination on one
    # line for every physical table* is what we forbid.
    for table in ("dwd_xxx_order_di", "dim_xxx_shop_df"):
        assert (
            f"`{table}` 的分区列、表粒度、唯一键" not in section_8
        ), f"section 8 should not demand full metadata for {table}"

    # Every ask in zero-profile mode is tagged with the section it unblocks
    # so the user can trace it back to a finding.
    bullet_lines = [
        line for line in section_8.splitlines()
        if line.strip().startswith("-")
    ]
    assert bullet_lines, "section 8 must contain at least one bullet"
    for line in bullet_lines:
        assert "【对应第" in line or "本次评审无被卡住" in line, (
            f"section-8 bullet missing finding-trace tag: {line!r}"
        )


def test_section_6_uses_candidate_label_when_semantics_unknown():
    """Section 6 must mark the rewrite as 候选改写方向 and explicitly say it
    is not directly executable when grain/keys/partitions/semantics are
    unknown."""

    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    md = render_markdown(run_static_check(sql))
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "候选改写方向" in section_6
    assert "不是可直接" in section_6 and "上线的 SQL" in section_6


def test_plain_sum_count_does_not_suggest_approx_distinct():
    """An ordinary SUM/COUNT SQL must not get COUNT DISTINCT or
    approx_distinct rewrite directions in section 6."""

    md = render_markdown(run_static_check(_PLAIN_AGG_SQL))
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "approx_distinct" not in section_6, (
        "ordinary SUM/COUNT SQL leaked an approx_distinct suggestion: "
        f"{section_6!r}"
    )
    assert "COUNT DISTINCT" not in section_6, (
        "ordinary SUM/COUNT SQL leaked a COUNT DISTINCT suggestion: "
        f"{section_6!r}"
    )
    # It should still surface the basic-aggregate direction.
    assert "基础聚合指标" in section_6


def test_count_distinct_still_suggests_distinct_or_approx():
    """COUNT DISTINCT SQL must still be able to surface distinct-user /
    approx-distinct rewrite directions when appropriate."""

    sql = _read(os.path.join(_EXAMPLES, "bad_count_distinct.sql"))
    md = render_markdown(run_static_check(sql))
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "去重指标改写" in section_6
    # At least one of the distinct-specific signals must be present.
    assert ("approx_distinct" in section_6) or ("中间表" in section_6)


def test_left_join_degradation_does_not_ask_for_unique_key():
    """A LEFT JOIN degraded by a WHERE on the right alias (J005) is fully
    detectable from SQL text — section 8 must not demand right-table
    unique-key confirmation just because a join_safety finding exists."""

    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    result = run_static_check(sql)

    rule_ids = {f.rule_id for f in result.findings}
    assert "J005" in rule_ids
    # Sanity: this example does not trigger row-explosion JOIN rules.
    assert rule_ids.isdisjoint({"J001", "J002", "J006", "J010"}), (
        f"unexpected row-explosion rules fired: {rule_ids}"
    )

    md = render_markdown(result)
    section_8 = _section(md, "## 8. 需要我补充的信息", "\n>")

    # The unique-key ask is the row-explosion ask — must not appear here.
    assert "JOIN 行数膨胀" not in section_8, (
        f"section 8 leaked a row-explosion ask for a pure J005 case:\n{section_8}"
    )
    assert "唯一键 / 关联键是否唯一" not in section_8, (
        f"section 8 demanded unique-key confirmation for J005:\n{section_8}"
    )


def test_left_join_degradation_asks_about_left_row_preservation():
    """The same case must surface a J005-specific ask about whether the
    business needs to preserve all left-table rows."""

    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    md = render_markdown(run_static_check(sql))
    section_8 = _section(md, "## 8. 需要我补充的信息", "\n>")

    assert "LEFT JOIN 退化" in section_8
    assert "保留左表全部行" in section_8


def test_left_join_degradation_section_6_omits_row_explosion_directions():
    """Section 6 for a J005-only case must not emit ROW_NUMBER dedup
    guidance.  MAPJOIN may appear iff a PF003 finding fires; for
    bad_left_join.sql PF003 *does* fire (right side is a `_df` dim), so
    we only assert the absence of ROW_NUMBER dedup language here."""

    sql = _read(os.path.join(_EXAMPLES, "bad_left_join.sql"))
    result = run_static_check(sql)
    rule_ids = {f.rule_id for f in result.findings}
    assert "J005" in rule_ids
    assert rule_ids.isdisjoint({"J001", "J002", "J006", "J010"})

    md = render_markdown(result)
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    # ROW_NUMBER is the row-explosion-specific dedup direction.
    assert "ROW_NUMBER" not in section_6, (
        "ROW_NUMBER dedup direction leaked into a pure-J005 section 6:\n"
        f"{section_6}"
    )
    # The J005-specific direction must be present.
    assert "LEFT JOIN 过滤位置" in section_6
    assert "ON 子句" in section_6


_CAST_IN_ON_SQL = """
SELECT a.user_id, b.shop_name
FROM dwd_xxx_order_di a
JOIN dim_xxx_shop_df b ON CAST(a.shop_id AS BIGINT) = b.shop_id
WHERE a.dt = '${bizdate}'
  AND b.dt = '${bizdate}';
"""


_NON_EQUALITY_ON_SQL = """
SELECT a.user_id, b.shop_name
FROM dwd_xxx_order_di a
JOIN dim_xxx_shop_df b ON a.shop_id < b.shop_id
WHERE a.dt = '${bizdate}'
  AND b.dt = '${bizdate}';
"""


def test_cast_in_on_keeps_unique_key_and_rewrite_guidance():
    """J003 (CAST in ON) must still surface the row-explosion / 关联键风险
    ask in section 8 and the row-explosion rewrite block in section 6,
    plus a CAST-specific rewrite direction.  Without this the user gets
    a J003 finding but no actionable guidance on how to fix it."""

    result = run_static_check(_CAST_IN_ON_SQL)
    rule_ids = {f.rule_id for f in result.findings}
    assert "J003" in rule_ids, f"J003 did not fire on CAST-in-ON SQL: {rule_ids}"

    md = render_markdown(result)
    section_8 = _section(md, "## 8. 需要我补充的信息", "\n>")
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "JOIN 行数膨胀" in section_8 and "唯一键" in section_8, (
        f"section 8 dropped the row-explosion ask for J003:\n{section_8}"
    )
    assert "JOIN 行数膨胀治理" in section_6, (
        f"section 6 dropped the row-explosion block for J003:\n{section_6}"
    )
    # The CAST-specific direction should be present so the user knows the
    # row-explosion guidance also covers their situation.
    assert "CAST" in section_6, (
        f"section 6 dropped the CAST-specific direction for J003:\n{section_6}"
    )


def test_unrecognized_on_keeps_unique_key_and_rewrite_guidance():
    """J004 (unrecognized equality keys, e.g. inequality / expression
    ON) must still surface the row-explosion / 关联键风险 ask in
    section 8 and the row-explosion rewrite block in section 6, plus an
    unknown-ON-specific rewrite direction."""

    result = run_static_check(_NON_EQUALITY_ON_SQL)
    rule_ids = {f.rule_id for f in result.findings}
    assert "J004" in rule_ids, (
        f"J004 did not fire on non-equality ON SQL: {rule_ids}"
    )

    md = render_markdown(result)
    section_8 = _section(md, "## 8. 需要我补充的信息", "\n>")
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "JOIN 行数膨胀" in section_8 and "唯一键" in section_8, (
        f"section 8 dropped the row-explosion ask for J004:\n{section_8}"
    )
    assert "JOIN 行数膨胀治理" in section_6, (
        f"section 6 dropped the row-explosion block for J004:\n{section_6}"
    )
    assert "未识别" in section_6 or "EXISTS" in section_6, (
        f"section 6 dropped the unrecognized-ON-specific direction for J004:"
        f"\n{section_6}"
    )


def test_row_explosion_case_still_asks_for_unique_key():
    """A row-explosion case (J010) must still ask for right-table
    unique-key confirmation in section 8 and surface ROW_NUMBER /
    pre-aggregation guidance in section 6."""

    sql = _read(os.path.join(_EXAMPLES, "bad_join_explosion.sql"))
    result = run_static_check(sql)
    rule_ids = {f.rule_id for f in result.findings}
    assert "J010" in rule_ids

    md = render_markdown(result)
    section_8 = _section(md, "## 8. 需要我补充的信息", "\n>")
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "JOIN 行数膨胀" in section_8
    assert "唯一键" in section_8
    assert "ROW_NUMBER" in section_6 or "GROUP BY" in section_6
    assert "JOIN 行数膨胀治理" in section_6


def test_performance_focus_keeps_long_period_distinct_guidance():
    """``--focus performance`` strips ``metric_definition`` findings, but
    PF006 (long-period COUNT DISTINCT) is a performance finding.  Section 6
    must therefore still emit the distinct / long-period direction and
    mention the rolling-table guidance, otherwise the user loses the most
    important rewrite guidance for this case."""

    sql = _read(os.path.join(_EXAMPLES, "bad_count_distinct.sql"))
    result = run_static_check(sql, focus="performance")

    # Sanity: focus actually filtered out metric_definition advisories.
    categories = {f.category for f in result.findings}
    assert "metric_definition" not in categories
    assert any(f.rule_id == "PF006" for f in result.findings), (
        "PF006 must remain in performance-only mode"
    )

    md = render_markdown(result)
    section_6 = _section(md, "## 6. 建议改写 SQL", "## 7.")

    assert "去重指标改写" in section_6, (
        "performance-only review dropped the long-period distinct rewrite "
        f"direction:\n{section_6}"
    )
    assert "中间表" in section_6, (
        "long-period distinct must surface the rolling-table direction"
    )
