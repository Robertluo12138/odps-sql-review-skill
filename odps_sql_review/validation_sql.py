"""validation_sql.py

Generate validation (验数) SQL templates that a data analyst can paste
into ODPS Studio / DataWorks before promoting their query to production.

We never execute these queries.  We only return them as plain text so
reviewers can review the SQL and run it themselves.

Templates intentionally use placeholders such as ``${bizdate}`` and
``<右表唯一键>`` so the analyst is forced to fill in real values rather
than blindly running.
"""

from __future__ import annotations

from typing import Dict, List

from .extract_joins import JoinClause
from .extract_metrics import Metric
from .extract_tables import TableReference


def _source_partition_row_count(table: TableReference) -> str:
    return (
        f"-- 1. 源表分区行数检查\n"
        f"SELECT COUNT(1) AS row_cnt\n"
        f"FROM   {table.name}\n"
        f"WHERE  dt = '${{bizdate}}'  -- 请替换为实际分区列与值\n;"
    )


def _join_key_null_check(joins: List[JoinClause]) -> List[str]:
    snippets: List[str] = []
    for join in joins:
        for left, right in join.join_keys:
            snippets.append(
                f"-- 2. JOIN 关联键空值检查 ({left}, {right})\n"
                f"SELECT SUM(CASE WHEN {left} IS NULL THEN 1 ELSE 0 END) AS left_null_cnt,\n"
                f"       SUM(CASE WHEN {right} IS NULL THEN 1 ELSE 0 END) AS right_null_cnt\n"
                f"FROM   {join.right_table} {join.right_alias or ''}\n"
                f"WHERE  dt = '${{bizdate}}'\n;"
            )
    return snippets


def _right_table_unique_check(joins: List[JoinClause]) -> List[str]:
    snippets: List[str] = []
    for join in joins:
        if not join.join_keys:
            continue
        right_keys = [right for _left, right in join.join_keys]
        keys = ", ".join(right_keys)
        snippets.append(
            f"-- 3. 右表 `{join.right_table}` 关联键重复检查\n"
            f"SELECT {keys}, COUNT(1) AS cnt\n"
            f"FROM   {join.right_table}\n"
            f"WHERE  dt = '${{bizdate}}'\n"
            f"GROUP BY {keys}\n"
            f"HAVING COUNT(1) > 1\n"
            f"LIMIT 50;"
        )
    return snippets


def _join_row_explosion_check(tables: List[TableReference], joins: List[JoinClause]) -> List[str]:
    if not joins:
        return []
    snippets: List[str] = []
    snippets.append(
        "-- 4. JOIN 前后行数膨胀对比\n"
        "-- 4a. JOIN 前左表行数\n"
        "SELECT COUNT(1) AS left_cnt\n"
        f"FROM   {tables[0].name if tables else '<左表>'}\n"
        "WHERE  dt = '${bizdate}'\n;\n\n"
        "-- 4b. JOIN 后行数\n"
        "SELECT COUNT(1) AS joined_cnt\n"
        "FROM   <在此粘贴你的 JOIN 子查询>\n"
        "WHERE  dt = '${bizdate}'\n;"
    )
    return snippets


def _target_primary_key_check() -> str:
    return (
        "-- 5. 目标表主键重复检查 (写入完成后执行)\n"
        "SELECT <主键字段>, COUNT(1) AS cnt\n"
        "FROM   <目标表>\n"
        "WHERE  dt = '${bizdate}'\n"
        "GROUP BY <主键字段>\n"
        "HAVING COUNT(1) > 1\n"
        "LIMIT 50;"
    )


def _core_field_null_check(metrics: List[Metric]) -> str:
    if not metrics:
        return (
            "-- 6. 核心字段空值检查\n"
            "SELECT SUM(CASE WHEN <核心字段> IS NULL THEN 1 ELSE 0 END) AS null_cnt,\n"
            "       COUNT(1) AS row_cnt\n"
            "FROM   <目标表>\n"
            "WHERE  dt = '${bizdate}'\n;"
        )
    fields = []
    for metric in metrics:
        if metric.metric_alias:
            fields.append(metric.metric_alias)
    fields = fields[:5] or ["<核心字段>"]
    expr = ",\n       ".join(
        f"SUM(CASE WHEN {f} IS NULL THEN 1 ELSE 0 END) AS {f}_null_cnt"
        for f in fields
    )
    return (
        "-- 6. 核心字段空值检查\n"
        f"SELECT {expr},\n"
        "       COUNT(1) AS row_cnt\n"
        "FROM   <目标表>\n"
        "WHERE  dt = '${bizdate}'\n;"
    )


def _yesterday_today_fluctuation(metrics: List[Metric]) -> str:
    if not metrics:
        metric_columns = "<核心指标>"
    else:
        metric_columns = ",\n       ".join(
            metric.metric_alias or f"<指标{idx + 1}>"
            for idx, metric in enumerate(metrics[:5])
        )
    return (
        "-- 7. 昨天 vs 今天指标波动检查\n"
        f"SELECT dt,\n       {metric_columns}\n"
        "FROM   <目标表>\n"
        "WHERE  dt IN ('${bizdate}', '${yyyymmdd_1}')\n"
        "ORDER BY dt\n;"
    )


def _partition_volume_check() -> str:
    return (
        "-- 8. 分区数据量检查 (评估是否异常大或异常小)\n"
        "SELECT dt, COUNT(1) AS row_cnt\n"
        "FROM   <目标表>\n"
        "WHERE  dt BETWEEN '${yyyymmdd_7}' AND '${bizdate}'\n"
        "GROUP BY dt\n"
        "ORDER BY dt\n;"
    )


def _count_distinct_validation(metrics: List[Metric]) -> List[str]:
    snippets: List[str] = []
    for metric in metrics:
        if metric.aggregate_type != "COUNT_DISTINCT":
            continue
        snippets.append(
            f"-- 9. COUNT DISTINCT 指标校验：`{metric.metric_alias or metric.metric_expr}`\n"
            "-- 与按用户粒度去重的中间表交叉验证\n"
            f"SELECT COUNT(DISTINCT user_id) AS exact_uv  -- 替换为实际去重列\n"
            "FROM   <目标用户行为表>\n"
            "WHERE  dt = '${bizdate}'\n;\n"
            "-- 同时建议用 approx_distinct 做对照：\n"
            "SELECT approx_distinct(user_id) AS approx_uv\n"
            "FROM   <目标用户行为表>\n"
            "WHERE  dt = '${bizdate}'\n;"
        )
    return snippets


def generate_validation_sql(
    tables: List[TableReference],
    joins: List[JoinClause],
    metrics: List[Metric],
) -> Dict[str, List[str]]:
    """Generate validation SQL templates grouped by purpose."""

    physical_tables = [t for t in tables if not t.is_cte]

    return {
        "source_partition_row_count": [
            _source_partition_row_count(table) for table in physical_tables
        ],
        "join_key_null_check": _join_key_null_check(joins),
        "right_table_unique_check": _right_table_unique_check(joins),
        "join_row_explosion": _join_row_explosion_check(physical_tables, joins),
        "target_primary_key": [_target_primary_key_check()],
        "core_field_null_check": [_core_field_null_check(metrics)],
        "yesterday_today_fluctuation": [_yesterday_today_fluctuation(metrics)],
        "partition_volume_check": [_partition_volume_check()],
        "count_distinct_validation": _count_distinct_validation(metrics),
    }


def render_validation_sql_markdown(
    snippets: Dict[str, List[str]],
) -> str:
    """Render the validation SQL dictionary as Markdown for the report."""

    titles = {
        "source_partition_row_count": "源分区行数",
        "join_key_null_check": "JOIN 关联键空值",
        "right_table_unique_check": "右表关联键唯一性",
        "join_row_explosion": "JOIN 前后行数膨胀",
        "target_primary_key": "目标表主键唯一",
        "core_field_null_check": "核心字段空值",
        "yesterday_today_fluctuation": "昨日今日指标波动",
        "partition_volume_check": "近 7 天分区数据量",
        "count_distinct_validation": "COUNT DISTINCT 校验",
    }
    lines: List[str] = []
    for key, items in snippets.items():
        if not items:
            continue
        lines.append(f"### {titles.get(key, key)}")
        for snippet in items:
            lines.append("```sql")
            lines.append(snippet.strip())
            lines.append("```")
        lines.append("")
    return "\n".join(lines).strip()
