"""report.py

Render a ``ReviewResult`` into the Chinese Markdown report defined in
``references/output_template.md``.

The Markdown structure must always include the eight required sections,
even when nothing is found, so the user can quickly scan the report.
We never produce a "looks good" verdict by itself; we always show the
risk level and the items that still need confirmation.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from .extract_metrics import Metric
from .rules import Finding
from .static_check import ReviewResult
from .validation_sql import generate_validation_sql, render_validation_sql_markdown


RISK_LABEL = {"high": "高", "medium": "中", "low": "低"}


# --------------------------------------------------------------------- #
# Markdown helpers                                                      #
# --------------------------------------------------------------------- #


def _format_finding(finding: Finding, with_blocking: bool = True) -> str:
    """Render a single finding as a Markdown bullet block."""

    blocking = "是" if finding.blocking else "否"
    confirm = "需要业务/数据确认" if finding.needs_confirmation else "可由静态分析判断"
    block_line = f"- 是否阻塞上线：{blocking}\n" if with_blocking else ""
    return (
        f"#### {finding.title}\n"
        f"- 风险点：{finding.title}\n"
        f"- 证据 SQL 片段：\n  ```sql\n  {finding.evidence}\n  ```\n"
        f"- 为什么危险：{finding.why}\n"
        f"- 建议修改方式：{finding.suggestion}\n"
        f"{block_line}"
        f"- 静态分析判定：{confirm}\n"
    )


def _section_findings(findings: List[Finding], level: str) -> str:
    items = [f for f in findings if f.risk_level == level]
    if not items:
        if level == "high":
            return "- 暂未发现高风险问题。仍需结合业务上下文 / LogView 复核。\n"
        if level == "medium":
            return "- 暂未发现中风险问题。\n"
        return "- 暂未发现低风险/可读性问题。\n"
    return "\n".join(_format_finding(f, with_blocking=(level != "low")) for f in items)


def _performance_block(findings: List[Finding]) -> str:
    """Group performance findings by sub-topic."""

    groups: Dict[str, List[Finding]] = {
        "分区裁剪": [],
        "Join 优化": [],
        "Group By / Count Distinct 优化": [],
        "年活 / 月活 / 长周期去重优化": [],
        "动态分区优化": [],
        "Reducer / Joiner / 并行度建议": [],
        "中间表 / 滚动表建议": [],
    }

    for finding in findings:
        if finding.category == "partition_pruning":
            groups["分区裁剪"].append(finding)
        elif finding.category == "join_safety":
            groups["Join 优化"].append(finding)
        elif finding.category == "performance" and finding.rule_id in {"PF005", "PF001"}:
            groups["Group By / Count Distinct 优化"].append(finding)
        elif finding.category == "performance" and finding.rule_id == "PF006":
            groups["年活 / 月活 / 长周期去重优化"].append(finding)
        elif finding.category == "insert_overwrite" and finding.rule_id == "I001":
            groups["动态分区优化"].append(finding)
        elif finding.category == "performance" and finding.rule_id in {"PF002", "PF003", "PF004"}:
            groups["Join 优化"].append(finding)
        elif finding.category == "performance":
            groups["Reducer / Joiner / 并行度建议"].append(finding)

    # Always include rolling-table guidance even if nothing matched.
    if not groups["中间表 / 滚动表建议"]:
        groups["中间表 / 滚动表建议"].append(
            Finding(
                rule_id="PF999",
                category="performance",
                risk_level="low",
                title="中间表 / 滚动表评估",
                evidence="-",
                why="长周期去重 / 多次复用同一聚合时，中间表能显著降低成本。",
                suggestion=(
                    "若同一聚合被多个报表复用，建议落地 dws_xxx_user_active_di 等中间表；"
                    "MAU/YAU 等指标考虑用户日活滚动表。需要结合数据量与业务复用度评估。"
                ),
                needs_confirmation=True,
            )
        )

    lines: List[str] = []
    for title, items in groups.items():
        lines.append(f"### {title}")
        if not items:
            lines.append("- 暂无明显问题（仍需结合 LogView / 数据量确认）。\n")
            continue
        for item in items:
            lines.append(
                f"- **{item.title}**\n"
                f"  - 现象：{item.why}\n"
                f"  - 建议：{item.suggestion}\n"
            )
        lines.append("")
    return "\n".join(lines).strip()


def _metric_block(metrics: List[Metric]) -> str:
    if not metrics:
        return "- 未识别到聚合指标，若 SQL 应当输出指标，请确认 SELECT 列表。\n"
    lines: List[str] = []
    for metric in metrics:
        lines.append(f"#### 指标：`{metric.metric_alias or metric.metric_expr}`")
        lines.append(f"- 表达式：`{metric.metric_expr}`")
        lines.append(f"- 聚合类型：{metric.aggregate_type}")
        lines.append(
            f"- 分子：{metric.possible_numerator if metric.possible_numerator else '需要确认'}"
        )
        lines.append(
            f"- 分母：{metric.possible_denominator if metric.possible_denominator else '需要确认（非比率指标可忽略）'}"
        )
        if metric.filters_in_case_when:
            lines.append("- CASE WHEN 内嵌过滤：")
            for f in metric.filters_in_case_when:
                lines.append(f"  - `{f}`")
        else:
            lines.append("- 过滤条件：未识别到 CASE WHEN 过滤，需要确认是否在 WHERE 中已过滤。")
        lines.append("- GROUP BY 粒度：需要结合外层 GROUP BY 确认")
        lines.append(
            "- 是否存在重复计算风险："
            + ("是，请确认 JOIN 关联键唯一性" if metric.aggregate_type in {"SUM", "COUNT"} else "需要确认")
        )
        lines.append(
            "- 是否需要业务确认："
            + ("是" if metric.need_business_confirm else "否")
        )
        if metric.note:
            lines.append(f"- 备注：{metric.note}")
        lines.append("")
    return "\n".join(lines).strip()


def _confirmation_items(result: ReviewResult) -> str:
    items: List[str] = []
    physical_tables = [t for t in result.tables if not t.is_cte]
    for t in physical_tables:
        items.append(f"`{t.name}` 的分区列、表粒度、唯一键、表大小级别（fact/dim、small/large）。")
    if any(m.aggregate_type == "COUNT_DISTINCT" for m in result.metrics):
        items.append("是否允许使用 approx_distinct（近似去重），还是必须精确。")
    items.append("目标表的主键、是否调度任务、调度日期变量。")
    items.append("LogView：总耗时、慢 stage 类型、reducer/joiner 数量、最大/平均运行时间、是否长尾。")
    return "\n".join(f"- {item}" for item in items)


# --------------------------------------------------------------------- #
# Public entry points                                                   #
# --------------------------------------------------------------------- #


def render_markdown(result: ReviewResult, include_validation: bool = True) -> str:
    """Render the review report as Markdown using the required template."""

    overall = RISK_LABEL.get(result.overall_risk, result.overall_risk)
    blocking_text = "否" if not result.blocking else "是"
    online_advice = "是" if (not result.blocking and result.overall_risk == "low") else "否"

    blocking_findings = [f for f in result.findings if f.blocking]
    blocking_summary = (
        "\n".join(f"  - {f.title}" for f in blocking_findings)
        or "  - 暂无明确阻塞点"
    )
    perf_findings = [f for f in result.findings if f.category == "performance"]
    perf_summary = (
        "\n".join(f"  - {f.title}" for f in perf_findings[:5])
        or "  - 暂未发现典型性能瓶颈，仍建议结合 LogView / 数据量复核"
    )

    confirm_summary_indented = "\n".join(
        f"  {line}" for line in _confirmation_items(result).splitlines()
    )

    parts: List[str] = []
    parts.append("# SQL 风险审查报告\n")

    parts.append("## 0. 总体结论")
    parts.append(f"- 总风险等级：{overall}")
    parts.append(f"- 是否建议直接上线：{online_advice}")
    parts.append("- 主要阻塞点：")
    parts.append(blocking_summary)
    parts.append("- 主要性能瓶颈：")
    parts.append(perf_summary)
    parts.append("- 需要确认项：")
    parts.append(confirm_summary_indented)
    parts.append("")

    parts.append("## 1. 高风险问题")
    parts.append(_section_findings(result.findings, "high"))
    parts.append("")

    parts.append("## 2. 中风险问题")
    parts.append(_section_findings(result.findings, "medium"))
    parts.append("")

    parts.append("## 3. 低风险 / 可读性问题")
    parts.append(_section_findings(result.findings, "low"))
    parts.append("")

    parts.append("## 4. 性能优化建议")
    parts.append(_performance_block(result.findings))
    parts.append("")

    parts.append("## 5. 指标口径解释")
    parts.append(_metric_block(result.metrics))
    parts.append("")

    parts.append("## 6. 建议改写 SQL")
    parts.append(
        "由于静态分析无法确认表粒度、唯一键、字段含义，下面只给出候选改写方向，"
        "请结合 table_profile.yml 和业务上下文确认后再落地：\n"
    )
    if any(f.category == "join_safety" for f in result.findings):
        parts.append(
            "- **JOIN 改写**：将右表过滤条件移到子查询中先过滤，再 JOIN；"
            "对右表先按 `unique_keys` 做 ROW_NUMBER 去重后再 JOIN，避免行数膨胀。\n"
        )
    if any(f.category == "metric_definition" for f in result.findings):
        parts.append(
            "- **指标改写**：将 COUNT DISTINCT 拆分为「先按用户日粒度去重，再按周期聚合」；"
            "比率指标显式处理分母为 0 (`nullif(b, 0)`)。\n"
        )
    if any(f.category == "partition_pruning" for f in result.findings):
        parts.append(
            "- **分区裁剪**：在最内层子查询补充分区过滤；"
            "去掉对分区列的函数包裹。\n"
        )
    if not result.findings:
        parts.append("- 当前未识别到显著问题，仍建议补充注释、明确指标口径。\n")
    parts.append("")

    parts.append("## 7. 上线前验数 SQL")
    if include_validation:
        snippets = generate_validation_sql(result.tables, result.joins, result.metrics)
        parts.append(render_validation_sql_markdown(snippets))
    else:
        parts.append("- 已按 --no-validation 选项跳过。可使用 --generate-validation-sql 生成。")
    parts.append("")

    parts.append("## 8. 需要我补充的信息")
    parts.append(_confirmation_items(result))
    parts.append("")

    parts.append(
        "> 本报告由 odps-sql-review 静态分析生成，未连接 ODPS。最终风险结论需要结合 "
        "数据量、LogView、业务口径、调度上下文与 table_profile.yml 复核。"
    )
    return "\n".join(parts).strip() + "\n"


def render_json(result: ReviewResult) -> str:
    """Render the report as machine-readable JSON."""

    payload = result.to_dict()
    payload["validation_sql"] = generate_validation_sql(
        result.tables, result.joins, result.metrics
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)
