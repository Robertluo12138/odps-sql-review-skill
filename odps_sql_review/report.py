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
    """Derive the minimum confirmation questions from the actual findings.

    Implements SKILL.md 3.4 (minimum-information principle) and the
    output-template contract for section 8 / section 0 ``需要确认项``:
    only ask for facts that are actually blocking a finding in *this*
    review — never emit a fixed company-wide checklist.
    """

    asks: List[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        if text not in seen:
            seen.add(text)
            asks.append(text)

    # Split join_safety findings by what they actually require to fix.
    # J005 ("LEFT JOIN degraded to INNER JOIN by WHERE on right alias") is
    # detected purely from SQL text — the only business question is
    # whether the user wants to preserve all left-table rows.
    #
    # The remaining JOIN rules all raise concerns about JOIN row counts
    # or relational integrity that the user must confirm against the
    # right table:
    #   J001/J002/J006/J010 — row-explosion / cartesian / many-to-many;
    #   J003               — CAST in ON may silently drop or duplicate rows;
    #   J004               — unrecognized ON condition (e.g. inequality /
    #                        expression-based JOIN) of unknown row impact.
    # All of them benefit from the same ask (right-table unique-key /
    # row-count integrity) and the same rewrite directions (dedup,
    # `unique_keys` declaration, V5 / V9 validation SQL).
    join_findings = [
        f for f in result.findings if f.category == "join_safety"
    ]
    has_left_join_degradation = any(
        f.rule_id == "J005" for f in join_findings
    )
    has_row_explosion_join = any(
        f.rule_id in {"J001", "J002", "J003", "J004", "J006", "J010"}
        for f in join_findings
    )
    has_partition = any(
        f.category == "partition_pruning" for f in result.findings
    )
    has_metric_def = any(
        f.category == "metric_definition" for f in result.findings
    )
    has_insert = any(
        f.category == "insert_overwrite" for f in result.findings
    )
    has_long_period_distinct = any(
        f.category == "performance" and f.rule_id == "PF006"
        for f in result.findings
    )
    # PF003 = MAPJOIN candidate (需要 size_level 确认), kept separate from
    # general LogView/parallelism asks per output_template.md §8 contract:
    # "表大小级别（仅在评估 MAPJOIN / DISTMAPJOIN 时索取）".
    mapjoin_findings = [
        f for f in result.findings
        if f.category == "performance" and f.rule_id == "PF003"
    ]
    has_mapjoin_candidate = bool(mapjoin_findings)
    has_perf_tuning = any(
        f.category == "performance"
        and f.rule_id not in {"PF003", "PF006", "PF999"}
        for f in result.findings
    )

    if has_left_join_degradation:
        add(
            "【对应第 1 节 LEFT JOIN 退化】仅需确认业务是否要保留左表全部行："
            "需要保留 → 把右表过滤条件移到 ON 子句或写成右表子查询；"
            "本来就只想要匹配项 → 直接把 LEFT JOIN 改成 INNER JOIN。"
            "该问题可由 SQL 文本直接判断，不需要额外的表元信息。"
        )
    if has_row_explosion_join:
        add(
            "【对应第 1/2 节 JOIN 行数膨胀 / 关联键风险】仅就触发"
            "行数膨胀（含 CAST 隐式过滤 / 未识别 ON 条件）的具体右表，"
            "确认其唯一键 / 关联键是否唯一以及 JOIN 后行数是否符合预期；"
            "不需要全表清单。"
        )
    if has_partition:
        add(
            "【对应第 1 节 分区风险】仅在分区列命名不在常见约定（"
            "dt/ds/pt/hh/bizdate/stat_dt 等）以内时，确认实际分区列。"
        )
    if has_metric_def:
        add(
            "【对应第 5 节 指标口径】仅就当前 SQL 中口径不清的具体指标，"
            "确认业务定义（含税/不含税、按 order_id 还是主单号、含/不含退款等）；"
            "不要把所有指标全量索取。"
        )
    if has_insert:
        add(
            "【对应第 1 节 INSERT OVERWRITE 风险】目标表的主键、是否调度"
            "任务，以及 ${bizdate} / ${yyyymmdd} 等调度日期变量的实际定义。"
        )
    if has_long_period_distinct:
        add(
            "【对应第 4 节 长周期去重】是否允许使用 `approx_distinct` "
            "近似去重；不允许时是否已落地按天聚合的中间表。"
        )
    if has_mapjoin_candidate:
        # Pull the candidate table names straight from the finding titles
        # so the ask names exactly the tables the user must size-check —
        # not a blanket "all dim tables" question.
        candidate_titles = "、".join(
            sorted({f.title for f in mapjoin_findings})
        )
        add(
            "【对应第 4 节 Join 优化 / MAPJOIN】仅就以下候选维度表确认其"
            f"实际大小级别（small / medium / large）：{candidate_titles}。"
            "small 才适合 `/*+ MAPJOIN */`，medium 评估 DISTMAPJOIN，"
            "large 不要开启。"
        )
    if has_perf_tuning:
        add(
            "【对应第 4 节 并行度建议】LogView 摘要（总耗时、慢 stage、"
            "reducer/joiner 数量、是否长尾），仅在你希望得到具体 reducer "
            "/ joiner 数字时提供；没有就保持 “需要结合 LogView / 数据量确认”。"
        )

    if not asks:
        return (
            "- 本次评审无被卡住的关键信息，仍建议在上线前结合 LogView / "
            "数据量复核第 4 节的性能建议。"
        )
    return "\n".join(f"- {a}" for a in asks)


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
    rewrite_directions: List[str] = []

    # JOIN-related rewrite directions — split by the underlying rule so we
    # don't suggest ROW_NUMBER dedup or MAPJOIN for a pure LEFT JOIN
    # degradation (J005) issue, which is detectable from SQL text alone
    # and only needs the filter to move to ON / a right-side subquery.
    join_findings_in_section_6 = [
        f for f in result.findings if f.category == "join_safety"
    ]
    has_left_join_degradation = any(
        f.rule_id == "J005" for f in join_findings_in_section_6
    )
    # Mirror the rule-id set in `_confirmation_items`: J003 (CAST in ON)
    # and J004 (unrecognized ON) also belong here because both can change
    # JOIN row counts in non-obvious ways and benefit from the dedup /
    # validation directions below.
    has_row_explosion_join = any(
        f.rule_id in {"J001", "J002", "J003", "J004", "J006", "J010"}
        for f in join_findings_in_section_6
    )
    has_mapjoin_candidate_for_rewrite = any(
        f.category == "performance" and f.rule_id == "PF003"
        for f in result.findings
    )

    if has_left_join_degradation:
        rewrite_directions.append(
            "- **候选改写方向：LEFT JOIN 过滤位置（待用户确认是否需要保留"
            "左表全部行后再定稿）**\n"
            "  - 方向 A（保留左表全部行）：把右表过滤条件从 WHERE 移到 "
            "ON 子句，未匹配的行右侧字段保持 NULL，例如 "
            "`LEFT JOIN b ON a.id = b.id AND b.status = 1`；\n"
            "  - 方向 B（保留左表全部行）：先把右表用子查询过滤好再 "
            "LEFT JOIN，例如 "
            "`LEFT JOIN (SELECT ... FROM b WHERE b.status = 1) b "
            "ON a.id = b.id`；\n"
            "  - 方向 C（业务实际需要 INNER 语义）：直接把 LEFT JOIN "
            "改成 INNER JOIN，并在第 7 节验数 SQL 中比对左表行数与 JOIN 后"
            "行数，确认丢失行数符合预期；\n"
            "  - 该问题由 SQL 文本即可判定，不需要表元信息；落地前只需"
            "在第 8 节确认是 A/B（保留左表）还是 C（确实要 INNER）。"
        )

    if has_row_explosion_join:
        rewrite_directions.append(
            "- **候选改写方向：JOIN 行数膨胀治理 / 关联键风险（待用户"
            "确认右表唯一键 / 关联键唯一性后再定稿；同样适用于 CAST "
            "在 ON、未识别等值 ON 条件等关联键风险）**\n"
            "  - 方向 A：右表先按候选唯一键做 `ROW_NUMBER` / `GROUP BY` "
            "去重，再 JOIN，从源头消除行数膨胀；\n"
            "  - 方向 B：必要时在 `table_profile.yml` 中声明右表 "
            "`unique_keys`（可选输入，仅用于让后续评审更精确）；\n"
            "  - 方向 C：在第 7 节验数 SQL 中加入 JOIN 前后行数对比与"
            "右表关联键唯一性检查（V5 / V9）；\n"
            "  - 方向 D（CAST 在 ON / 未识别 ON 条件适用）：把两侧字段"
            "类型对齐到上游 CTE / 视图，去掉 ON 中的 `CAST`；非等值或表达式"
            "关联请改写为 `EXISTS` / 预聚合 / 等值化 ON，必要时 V5 行数"
            "对比同时核对 CAST / 表达式对齐前后是否变化；\n"
            "  - 落地前请在第 8 节确认右表关联键是否唯一以及 ON 条件是否"
            "为干净的等值关联。"
        )

    if has_mapjoin_candidate_for_rewrite:
        rewrite_directions.append(
            "- **候选改写方向：MAPJOIN（待用户确认右表大小级别后再定稿）**\n"
            "  - 右表确为小表（一般 < 几十万行 / 单 worker 内存可装下）时，"
            "在 SELECT 后加 `/*+ MAPJOIN(<右表别名>) */` 避免 shuffle；\n"
            "  - 中等规模可评估 DISTMAPJOIN，large 不要开启；\n"
            "  - 不要同时使用 MAPJOIN 与 SKEW JOIN；\n"
            "  - 落地前请在第 8 节确认候选维度表的实际大小级别。"
        )
    # Tailor metric-rewrite directions to the metric flavours actually
    # present in the SQL.  Each direction is gated independently so that
    # each focus mode keeps the right guidance:
    #   - correctness focus strips ``performance`` findings — but
    #     ``metric_definition`` advisories (M00x) remain, so ratio /
    #     basic-aggregate / generic distinct directions still fire when
    #     the relevant metric flavour exists.
    #   - performance focus strips ``metric_definition`` findings — but
    #     PF006 (long-period COUNT DISTINCT) may still fire, so we must
    #     keep the long-period distinct direction available.
    metric_def_present = any(
        f.category == "metric_definition" for f in result.findings
    )
    has_count_distinct_metric = any(
        m.aggregate_type in {"COUNT_DISTINCT", "APPROX_DISTINCT"}
        for m in result.metrics
    )
    has_long_period_distinct_finding = any(
        f.category == "performance" and f.rule_id == "PF006"
        for f in result.findings
    )
    has_ratio_metric = any(
        m.aggregate_type == "RATIO" for m in result.metrics
    )
    has_basic_aggregate_metric = any(
        m.aggregate_type in {"SUM", "COUNT", "AVG"}
        for m in result.metrics
    )

    trigger_distinct = (
        (metric_def_present and has_count_distinct_metric)
        or has_long_period_distinct_finding
    )
    trigger_ratio = metric_def_present and has_ratio_metric
    trigger_basic = metric_def_present and has_basic_aggregate_metric
    trigger_generic_metric = metric_def_present and not (
        has_count_distinct_metric
        or has_ratio_metric
        or has_basic_aggregate_metric
    )

    if trigger_distinct:
        distinct_lines = [
            "- **候选改写方向：去重指标改写（待用户确认指标业务定义"
            "与是否允许近似去重后再定稿）**",
            "  - 方向 A：将 COUNT DISTINCT 拆为 “先按最小粒度（如 "
            "dt + user_id）去重，再按周期聚合”；禁止把日 UV 直接相加"
            "得到 MAU / YAU；",
        ]
        if has_long_period_distinct_finding:
            distinct_lines.append(
                "  - 方向 B：长周期去重时落地按天聚合的中间表（如 "
                "`dws_xxx_user_active_di`，dt + user_id 粒度），在中间"
                "表上计算月活 / 年活；"
            )
        distinct_lines.append(
            "  - 方向 C：业务允许近似（非财务 / 合规场景）时改用 "
            "`approx_distinct`，并在指标命名上体现 `_approx`；"
        )
        distinct_lines.append(
            "  - 落地前请在第 8 节确认指标的精确业务定义，以及"
            "是否允许近似去重。"
        )
        rewrite_directions.append("\n".join(distinct_lines))

    if trigger_ratio:
        rewrite_directions.append(
            "- **候选改写方向：比率指标改写（待用户确认分子分母同粒度"
            "后再定稿）**\n"
            "  - 方向 A：显式处理分母为 0，如 `分子 / nullif(分母, 0)`，"
            "避免 NULL 污染；\n"
            "  - 方向 B：与业务方确认分子与分母是否同粒度、同过滤条件"
            "（同一时间窗、同一用户口径）；\n"
            "  - 落地前请在第 8 节确认比率指标的业务定义（比率应 ≤ 1 "
            "还是允许 > 1，分子是否为分母的子集）。"
        )

    if trigger_basic:
        rewrite_directions.append(
            "- **候选改写方向：基础聚合指标（SUM / COUNT / AVG）改写"
            "（待用户确认 JOIN 唯一键与指标业务定义后再定稿）**\n"
            "  - 方向 A：确认参与聚合的明细行是否被 JOIN 放大；必要时"
            "先在最小粒度去重 / 预聚合，再 JOIN，避免 SUM / COUNT 重复"
            "计算；\n"
            "  - 方向 B：与业务方确认指标口径（含税 / 不含税、是否含"
            "退款 / 优惠券、按 order_id 还是主单号统计），保证 SUM / "
            "COUNT 的语义一致；\n"
            "  - 方向 C：在第 7 节验数 SQL 中加入 JOIN 前后行数对比与"
            "核心字段空值检查，提前发现 NULL / 重复行污染聚合结果；\n"
            "  - 落地前请在第 8 节确认相关右表的唯一键以及指标的精确"
            "业务定义。"
        )

    if trigger_generic_metric:
        # metric_definition findings exist but nothing in result.metrics
        # matched a known flavour (e.g. CASE_WHEN-only).  Fall back to a
        # generic, non-leading direction.
        rewrite_directions.append(
            "- **候选改写方向：指标语义复核（待用户确认指标业务定义"
            "后再定稿）**\n"
            "  - 与业务方确认指标业务定义、过滤条件与统计粒度；\n"
            "  - 在第 7 节验数 SQL 中加入指标波动检查与空值检查。"
        )
    if any(f.category == "partition_pruning" for f in result.findings):
        rewrite_directions.append(
            "- **候选改写方向：分区裁剪（在常见分区命名下可直接落地，"
            "其它情况待第 8 节确认实际分区列后再定稿）**\n"
            "  - 方向 A：在最内层子查询补充分区过滤；\n"
            "  - 方向 B：去掉对分区列的函数包裹，把变换放到外层。"
        )

    if rewrite_directions:
        parts.append(
            "> 默认零 profile 模式：以下仅为候选改写方向，**不是可直接\n"
            "> 上线的 SQL**。在表粒度 / 唯一键 / 分区列 / 字段语义全部由\n"
            "> 用户确认前，请勿直接粘贴落地。\n"
        )
        parts.append("\n".join(rewrite_directions) + "\n")
    else:
        parts.append(
            "- 暂未识别到显著正确性 / 性能问题；本次无需改写。\n"
            "  仍建议补充注释、明确指标口径，并在上线前结合数据量复核。\n"
        )
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
        "> 本报告由 odps-sql-review 静态分析生成，未连接 ODPS。零 profile "
        "模式下仅依据 SQL 文本判断；最终风险结论需要结合数据量、LogView、"
        "业务口径与调度上下文复核（table_profile 为可选输入，非前置条件）。"
    )
    return "\n".join(parts).strip() + "\n"


def render_json(result: ReviewResult) -> str:
    """Render the report as machine-readable JSON."""

    payload = result.to_dict()
    payload["validation_sql"] = generate_validation_sql(
        result.tables, result.joins, result.metrics
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)
