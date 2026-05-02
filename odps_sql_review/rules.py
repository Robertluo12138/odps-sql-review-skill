"""rules.py

Rule definitions for the static review.

Each rule produces zero or more ``Finding`` objects.  A Finding is a
plain dataclass that can be serialized to JSON and rendered to
Markdown by ``report.py``.

The rule set focuses on ODPS / MaxCompute idioms:

* partition pruning
* JOIN safety (LEFT-JOIN-becomes-INNER, row explosion, ON 1=1)
* metric duplication after joins
* COUNT DISTINCT and long-period distinct user metrics
* GROUP BY / ROW_NUMBER / dynamic-partition skew
* INSERT OVERWRITE PARTITION risks
* readability / low-risk hygiene

Rules deliberately produce *advice in Chinese* because the target
audience is Chinese internet-company data analysts and engineers.
Risk levels:

* "high"   - must be fixed before going to production;
* "medium" - should be reviewed and very likely improved;
* "low"    - readability or hygiene; no production impact.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .extract_joins import JoinClause
from .extract_metrics import Metric
from .extract_tables import TableReference
from .parser import ParsedSQL
from .utils import normalize_whitespace, truncate


CATEGORY_PARTITION = "partition_pruning"
CATEGORY_JOIN = "join_safety"
CATEGORY_METRIC = "metric_definition"
CATEGORY_INSERT = "insert_overwrite"
CATEGORY_PERFORMANCE = "performance"
CATEGORY_READABILITY = "readability"
CATEGORY_DATE = "date_boundary"


@dataclass
class Finding:
    """A single review finding produced by a rule."""

    rule_id: str
    category: str
    risk_level: str  # "high" / "medium" / "low"
    title: str
    evidence: str
    why: str
    suggestion: str
    blocking: bool = False
    needs_confirmation: bool = False

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


def _evidence(text: str, length: int = 240) -> str:
    """Format an evidence snippet so the report stays readable."""

    return truncate(normalize_whitespace(text), length)


# --------------------------------------------------------------------- #
# Partition rules                                                       #
# --------------------------------------------------------------------- #


def rule_missing_partition_filter(
    parsed: ParsedSQL, tables: List[TableReference]
) -> List[Finding]:
    """Flag physical source tables that lack any partition filter."""

    findings: List[Finding] = []
    for table in tables:
        if table.is_cte:
            continue
        # We only know about *expected* partition columns from heuristics.
        # If a user provides a table profile, the static_check layer
        # will pre-fill ``has_partition_filter`` accordingly.
        if table.has_partition_filter is False:
            findings.append(
                Finding(
                    rule_id="P001",
                    category=CATEGORY_PARTITION,
                    risk_level="high",
                    title=f"源表 `{table.name}` 未发现分区过滤",
                    evidence=_evidence(
                        f"FROM/JOIN {table.name}"
                        + (f" AS {table.alias}" if table.alias else "")
                    ),
                    why=(
                        "ODPS 上未带 dt/ds/pt/hh/bizdate 等分区过滤会触发全表扫描，"
                        "可能产生超大读量、占用集群资源、严重时直接被限流或失败。"
                    ),
                    suggestion=(
                        "在最内层子查询里对该表的分区列添加过滤，例如 "
                        "`WHERE dt = '${bizdate}'`。"
                        "若确为维度表请在 `table_profile.yml` 中标注 size_level=small / dimension，"
                        "或者明确补充注释说明无分区。"
                    ),
                    blocking=True,
                    needs_confirmation=True,
                )
            )
        elif any("(wrapped)" in col for col in table.partition_filter_columns):
            findings.append(
                Finding(
                    rule_id="P002",
                    category=CATEGORY_PARTITION,
                    risk_level="medium",
                    title=f"`{table.name}` 的分区列被函数包裹，可能失去分区裁剪",
                    evidence=_evidence(table.note or ""),
                    why=(
                        "对分区列使用 substr / date_format / cast 等函数后，"
                        "ODPS 优化器无法识别分区谓词，会退化为全分区扫描。"
                    ),
                    suggestion=(
                        "请改写为对分区列直接做等值或区间比较，例如 "
                        "`dt BETWEEN '20240101' AND '20240131'`，把月份截断逻辑移到外层。"
                    ),
                )
            )
    return findings


def rule_long_partition_window(parsed: ParsedSQL) -> List[Finding]:
    """Flag suspicious 30/365 day windows in partition filters."""

    findings: List[Finding] = []
    text = parsed.stripped.lower()
    long_window_patterns = [
        (
            r"date_sub\([^,]+,\s*(\d{2,4})\s*\)",
            "DATE_SUB 推算的窗口",
        ),
        (
            r"between\s+'?(\d{8,10})'?\s+and\s+'?(\d{8,10})'?",
            "BETWEEN 写死的日期范围",
        ),
    ]
    for pattern, description in long_window_patterns:
        for match in re.finditer(pattern, text):
            try:
                if pattern.startswith("date_sub"):
                    days = int(match.group(1))
                    if days >= 30:
                        findings.append(
                            Finding(
                                rule_id="P003",
                                category=CATEGORY_PARTITION,
                                risk_level="medium",
                                title=f"检测到 {description}，窗口约 {days} 天，可能产生大扫描",
                                evidence=_evidence(match.group(0)),
                                why=(
                                    "30 天以上的明细分区扫描在大表上数据量极大，"
                                    "若是计算长周期去重指标更容易拖垮任务。"
                                ),
                                suggestion=(
                                    "评估是否可以使用 `dws_xxx_user_active_di` 这类按天聚合的中间表，"
                                    "或建立用户日活滚动表来支撑 MAU / YAU；"
                                    "如确需扫描原始明细，请结合 LogView 与数据量评估并行度。"
                                ),
                                needs_confirmation=True,
                            )
                        )
                else:
                    start = match.group(1)
                    end = match.group(2)
                    if len(start) == 8 and len(end) == 8:
                        try:
                            from datetime import datetime

                            start_dt = datetime.strptime(start, "%Y%m%d")
                            end_dt = datetime.strptime(end, "%Y%m%d")
                            if (end_dt - start_dt).days >= 30:
                                findings.append(
                                    Finding(
                                        rule_id="P003",
                                        category=CATEGORY_PARTITION,
                                        risk_level="medium",
                                        title=(
                                            f"检测到 BETWEEN 跨度约 {(end_dt - start_dt).days} 天，"
                                            "属于长周期扫描"
                                        ),
                                        evidence=_evidence(match.group(0)),
                                        why=(
                                            "长周期扫描会显著增加 IO，配合 COUNT DISTINCT / "
                                            "GROUP BY 容易引发倾斜和长尾。"
                                        ),
                                        suggestion=(
                                            "考虑使用按天聚合的中间表 (dws_xxx_user_active_di)，"
                                            "或拆解为多个滚动窗口；"
                                            "并结合 LogView 评估并行度与资源是否需要调整。"
                                        ),
                                        needs_confirmation=True,
                                    )
                                )
                        except ValueError:
                            pass
            except (IndexError, ValueError):
                continue
    return findings


# --------------------------------------------------------------------- #
# JOIN rules                                                            #
# --------------------------------------------------------------------- #


def rule_join_safety(parsed: ParsedSQL, joins: List[JoinClause]) -> List[Finding]:
    """Translate JoinClause.risks into Findings, plus extra deductions."""

    findings: List[Finding] = []
    for join in joins:
        for risk in join.risks:
            risk_level = "high"
            blocking = True
            rule_id = "J001"
            if "OR" in risk and "ON" in risk:
                rule_id = "J002"
            if "CAST" in risk:
                rule_id = "J003"
                risk_level = "medium"
                blocking = False
            if "未识别到清晰" in risk:
                rule_id = "J004"
                risk_level = "medium"
                blocking = False
            if "退化为 INNER JOIN" in risk:
                rule_id = "J005"
            if "ON 1=1" in risk or "笛卡尔积" in risk:
                rule_id = "J006"
            findings.append(
                Finding(
                    rule_id=rule_id,
                    category=CATEGORY_JOIN,
                    risk_level=risk_level,
                    title=f"JOIN 风险：`{join.right_table}`",
                    evidence=_evidence(join.raw_clause),
                    why=risk,
                    suggestion=(
                        "请明确两侧的关联粒度、是否需要去重、是否需要把过滤放到子查询。"
                        "对于必须保留左表全部行的需求，过滤条件应放在 ON 子句或写成右表子查询。"
                    ),
                    blocking=blocking,
                    needs_confirmation=True,
                )
            )

    # Any JOIN where the right table looks like a fact table (not a
    # dimension) should raise a row-explosion concern.  The user can
    # silence this by declaring `unique_keys` in table_profile.yml.
    fact_joins = [
        j
        for j in joins
        if not any(tag in j.right_table.lower() for tag in ("dim_", "_dim", "dimension", "_df"))
    ]
    if fact_joins:
        findings.append(
            Finding(
                rule_id="J010",
                category=CATEGORY_JOIN,
                risk_level="medium",
                title=(
                    f"检测到 {len(fact_joins)} 个事实表 JOIN，需要确认右表唯一键"
                ),
                evidence=_evidence(
                    " ; ".join(j.raw_clause for j in fact_joins[:3])
                ),
                why=(
                    "事实表之间 JOIN 时若右表关联键不唯一会出现行数膨胀，"
                    "下游 SUM / COUNT 等聚合容易出现重复计算（典型场景：订单 JOIN 支付，"
                    "一笔订单可能对应多次支付）。"
                ),
                suggestion=(
                    "请确认每张右表在关联键上的唯一性，可在 table_profile.yml 中声明 unique_keys，"
                    "或在 JOIN 之前对右表先做 ROW_NUMBER 去重 / 预聚合。"
                ),
                needs_confirmation=True,
            )
        )
    return findings


# --------------------------------------------------------------------- #
# Metric rules                                                          #
# --------------------------------------------------------------------- #


def rule_metric_definition(metrics: List[Metric]) -> List[Finding]:
    """Add advisory findings for each metric expression."""

    findings: List[Finding] = []
    distinct_count = 0
    for metric in metrics:
        if metric.aggregate_type == "COUNT_DISTINCT":
            distinct_count += 1
            findings.append(
                Finding(
                    rule_id="M001",
                    category=CATEGORY_METRIC,
                    risk_level="medium",
                    title=f"COUNT DISTINCT 指标：`{metric.metric_alias or metric.metric_expr}`",
                    evidence=_evidence(metric.metric_expr),
                    why=(
                        "COUNT DISTINCT 在大数据量下常成为单 reducer 长尾热点，"
                        "若是 MAU/YAU/30/365 天去重指标更容易超时。"
                    ),
                    suggestion=(
                        "若业务允许近似值，可以使用 approx_distinct，但要明确说明不适用于财务等强精确场景。"
                        "若要求精确，请先在最小粒度 (dt + user_id) 上去重，再对周期窗口聚合，"
                        "禁止把日 UV 直接相加得到 MAU/YAU。"
                    ),
                    needs_confirmation=True,
                )
            )
        elif metric.aggregate_type == "RATIO":
            findings.append(
                Finding(
                    rule_id="M002",
                    category=CATEGORY_METRIC,
                    risk_level="medium",
                    title=f"比率指标：`{metric.metric_alias or metric.metric_expr}`",
                    evidence=_evidence(metric.metric_expr),
                    why=(
                        "比率指标的分子 / 分母粒度需要严格一致，分母为 0 时会得到 NULL 或异常值。"
                    ),
                    suggestion=(
                        f"请确认分子: `{metric.possible_numerator}`，分母: `{metric.possible_denominator}` 是否同粒度，"
                        "并在 SQL 中处理分母为 0 的场景，例如 `nullif(b, 0)`。"
                    ),
                    needs_confirmation=True,
                )
            )
        elif metric.aggregate_type in {"SUM", "COUNT", "AVG"}:
            findings.append(
                Finding(
                    rule_id="M003",
                    category=CATEGORY_METRIC,
                    risk_level="low",
                    title=f"基础聚合指标：`{metric.metric_alias or metric.metric_expr}`",
                    evidence=_evidence(metric.metric_expr),
                    why=(
                        "基础聚合指标在多表 JOIN 后可能因右表非唯一导致重复计算。"
                    ),
                    suggestion=(
                        "请在 table_profile.yml 中确认右表唯一键；"
                        "并补充验数 SQL 比较 JOIN 前后行数。"
                    ),
                    needs_confirmation=True,
                )
            )
        if metric.note:
            findings.append(
                Finding(
                    rule_id="M004",
                    category=CATEGORY_METRIC,
                    risk_level="medium",
                    title=f"长周期去重指标：`{metric.metric_alias or metric.metric_expr}`",
                    evidence=_evidence(metric.metric_expr),
                    why=metric.note,
                    suggestion=(
                        "建议落地用户日活中间表 dws_xxx_user_active_di (dt + user_id)，"
                        "再在该表上做 MAU / YAU；如要近似值请使用 approx_distinct 并在文档中注明。"
                    ),
                    needs_confirmation=True,
                )
            )
    if distinct_count >= 2:
        findings.append(
            Finding(
                rule_id="M005",
                category=CATEGORY_METRIC,
                risk_level="medium",
                title=f"同一查询出现 {distinct_count} 个 COUNT DISTINCT",
                evidence=f"COUNT DISTINCT 数量：{distinct_count}",
                why=(
                    "多个 COUNT DISTINCT 会触发多次去重 shuffle，资源占用与 IO 显著放大。"
                ),
                suggestion=(
                    "评估是否可以拆分为独立查询、使用 GROUPING SETS、或先做用户粒度去重再聚合；"
                    "近似可接受时使用 approx_distinct。"
                ),
            )
        )
    return findings


# --------------------------------------------------------------------- #
# INSERT OVERWRITE rules                                                #
# --------------------------------------------------------------------- #


def rule_insert_overwrite(parsed: ParsedSQL) -> List[Finding]:
    findings: List[Finding] = []
    text = parsed.stripped
    lower = text.lower()
    if "insert overwrite" not in lower:
        return findings

    # Static target partition: PARTITION (dt='${bizdate}')
    static_partition = re.search(
        r"partition\s*\(\s*([A-Za-z_][\w]*)\s*=\s*'?(\$\{[^}]+\}|[^,'\)]+)'?\s*\)",
        lower,
    )
    if not static_partition:
        # Could be dynamic partition.
        if re.search(r"partition\s*\(\s*[A-Za-z_][\w]*\s*\)", lower):
            findings.append(
                Finding(
                    rule_id="I001",
                    category=CATEGORY_INSERT,
                    risk_level="high",
                    title="检测到动态分区 INSERT OVERWRITE",
                    evidence=_evidence(text[: text.lower().find("partition") + 80]),
                    why=(
                        "动态分区如果分区数量不可控，可能写入到错误分区或产生大量小文件，"
                        "线上事故常见来源。"
                    ),
                    suggestion=(
                        "1) 在 SQL 顶部明确预期的分区数量；"
                        "2) 评估是否可以改成静态分区+多次写入；"
                        "3) 必要时设置 odps.sql.reshuffle.dynamicpt=false 减少 reducer，"
                        "但要警惕小文件问题，需要结合数据量确认。"
                    ),
                    blocking=True,
                    needs_confirmation=True,
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="I002",
                    category=CATEGORY_INSERT,
                    risk_level="high",
                    title="INSERT OVERWRITE 未指定目标分区",
                    evidence=_evidence(text[: lower.find("insert overwrite") + 120]),
                    why=(
                        "未明确分区会覆盖整张表，是非常严重的生产事故，必须立即修复。"
                    ),
                    suggestion=(
                        "请补充 PARTITION (dt='${bizdate}') 等目标分区指定，并核实变量值。"
                    ),
                    blocking=True,
                )
            )
    else:
        # Static partition - just remind about variable substitution.
        findings.append(
            Finding(
                rule_id="I003",
                category=CATEGORY_INSERT,
                risk_level="low",
                title="确认目标分区变量是否正确",
                evidence=_evidence(static_partition.group(0)),
                why=(
                    "调度任务里日期变量替换错误可能写入到错误的分区。"
                ),
                suggestion=(
                    "请核实 ${bizdate} / ${yyyymmdd} 等变量是否与调度系统对齐；"
                    "建议在验数 SQL 中检查目标分区的行数与基础指标。"
                ),
                needs_confirmation=True,
            )
        )
    return findings


# --------------------------------------------------------------------- #
# Performance rules                                                     #
# --------------------------------------------------------------------- #


def rule_performance(parsed: ParsedSQL, joins: List[JoinClause], metrics: List[Metric]) -> List[Finding]:
    findings: List[Finding] = []
    text = parsed.stripped
    lower = text.lower()

    # GROUP BY skew hint when GROUP BY is present and dataset suspected large.
    if re.search(r"\bgroup\s+by\b", lower):
        findings.append(
            Finding(
                rule_id="PF001",
                category=CATEGORY_PERFORMANCE,
                risk_level="low",
                title="GROUP BY 存在，需要确认是否存在倾斜热点 key",
                evidence=_evidence("GROUP BY 子句"),
                why=(
                    "GROUP BY 当某个 key (如默认值、空值、热门商品) 数据量远超其他 key 时，"
                    "对应的 reducer 会成为长尾。"
                ),
                suggestion=(
                    "若已有 LogView 显示个别 reducer 慢，可考虑：\n"
                    "  - 二阶段聚合 (加盐预聚合再二次聚合)；\n"
                    "  - SET odps.sql.groupby.skewindata=true (确认存在倾斜后再开)；\n"
                    "  - 单独剥离热点 key 处理。\n"
                    "需要 LogView / 数据量确认后再决定。"
                ),
                needs_confirmation=True,
            )
        )

    # ROW_NUMBER skew hint.
    for match in re.finditer(
        r"row_number\s*\(\s*\)\s*over\s*\(\s*partition\s+by\s+([^)]+)\)",
        lower,
    ):
        partition_keys = match.group(1)
        findings.append(
            Finding(
                rule_id="PF002",
                category=CATEGORY_PERFORMANCE,
                risk_level="medium",
                title="ROW_NUMBER PARTITION BY 可能产生倾斜",
                evidence=_evidence(match.group(0)),
                why=(
                    f"ROW_NUMBER 按 `{partition_keys.strip()}` 分组排序时，"
                    "若分组键分布不均会触发长尾任务。"
                ),
                suggestion=(
                    "1) 评估只取 TopN 时是否可以使用预聚合或两阶段方式；\n"
                    "2) 对热点 key 单独处理；\n"
                    "3) 如确实需要全量去重，请结合 LogView 与并行度参数调整。"
                ),
                needs_confirmation=True,
            )
        )

    # JOIN skew / MAPJOIN candidate (rough heuristic: small <-> large naming).
    for join in joins:
        right_lower = join.right_table.lower()
        if any(tag in right_lower for tag in ("dim_", "_dim", "dimension", "_df")):
            findings.append(
                Finding(
                    rule_id="PF003",
                    category=CATEGORY_PERFORMANCE,
                    risk_level="low",
                    title=f"维度表 `{join.right_table}` 可能适合 MAPJOIN",
                    evidence=_evidence(join.raw_clause),
                    why=(
                        "维度表通常较小，使用 MAPJOIN 可以避免 shuffle，显著加速 JOIN。"
                    ),
                    suggestion=(
                        f"在 SELECT 后加 `/*+ MAPJOIN({join.right_alias or join.right_table.split('.')[-1]}) */`，"
                        "确认维度表大小可以放入 worker 内存 (默认 512MB) 后再开启；"
                        "中等大小的表可以评估 DISTMAPJOIN。"
                    ),
                    needs_confirmation=True,
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="PF004",
                    category=CATEGORY_PERFORMANCE,
                    risk_level="low",
                    title=f"大表 JOIN 大表 `{join.right_table}`，建议预过滤 / 预聚合",
                    evidence=_evidence(join.raw_clause),
                    why=(
                        "两张大表直接 JOIN 会产生大 shuffle，且热点 key 容易造成 SKEW JOIN。"
                    ),
                    suggestion=(
                        "1) 提前过滤无关分区与字段；\n"
                        "2) 在子查询里只 SELECT 需要的列；\n"
                        "3) 若已知热点 key 可考虑 SET odps.sql.skewjoin=true 或手工拆分；\n"
                        "需要 LogView / 数据量确认。"
                    ),
                    needs_confirmation=True,
                )
            )

    # Multiple COUNT DISTINCT - already raised by metrics, but echo for performance section.
    if sum(1 for m in metrics if m.aggregate_type == "COUNT_DISTINCT") >= 2:
        findings.append(
            Finding(
                rule_id="PF005",
                category=CATEGORY_PERFORMANCE,
                risk_level="medium",
                title="多个 COUNT DISTINCT 影响并行度",
                evidence="同一查询包含多个 COUNT DISTINCT",
                why=(
                    "ODPS 对 COUNT DISTINCT 通常退化为单 reducer，多个 distinct 会串行排队。"
                ),
                suggestion=(
                    "评估是否可以拆分为多个临时表后 UNION ALL；\n"
                    "或在用户粒度先去重再聚合；\n"
                    "近似指标可以使用 approx_distinct。"
                ),
                needs_confirmation=True,
            )
        )

    # Long window + COUNT DISTINCT specific advice.
    if any(metric.aggregate_type == "COUNT_DISTINCT" for metric in metrics) and (
        "date_sub" in lower or "between" in lower
    ):
        findings.append(
            Finding(
                rule_id="PF006",
                category=CATEGORY_PERFORMANCE,
                risk_level="high",
                title="长周期 + COUNT DISTINCT 是典型超时组合",
                evidence=_evidence("COUNT DISTINCT 与长周期窗口同时出现"),
                why=(
                    "在原始明细表上对 30/365 天分区做 COUNT DISTINCT，"
                    "数据量与 shuffle 量极大，是最常见的任务超时原因。"
                ),
                suggestion=(
                    "1) 优先落地按天聚合的中间表 (dt + user_id)；\n"
                    "2) 长窗口指标基于该中间表二次去重；\n"
                    "3) 对纯监控类指标可使用 approx_distinct；\n"
                    "4) 严格精确的财务指标禁止把日 UV 累加为 MAU。"
                ),
                blocking=True,
                needs_confirmation=True,
            )
        )

    # ORDER BY without LIMIT.
    if re.search(r"\border\s+by\b", lower) and not re.search(r"\blimit\b", lower):
        findings.append(
            Finding(
                rule_id="PF007",
                category=CATEGORY_PERFORMANCE,
                risk_level="medium",
                title="ORDER BY 没有 LIMIT，会触发全局排序",
                evidence=_evidence("ORDER BY ..."),
                why=(
                    "ODPS 中 ORDER BY 是单 reducer 全局排序，没有 LIMIT 会非常慢甚至 OOM。"
                ),
                suggestion=(
                    "若只需 TopN 请加上 LIMIT；\n"
                    "若需要分组内排序请使用 DISTRIBUTE BY + SORT BY 或 ROW_NUMBER。"
                ),
            )
        )

    # SELECT * detection.
    if re.search(r"select\s+\*\s+from", lower):
        findings.append(
            Finding(
                rule_id="PF008",
                category=CATEGORY_PERFORMANCE,
                risk_level="low",
                title="检测到 SELECT *",
                evidence=_evidence("SELECT * FROM ..."),
                why=(
                    "ODPS 列存储下，SELECT * 会读取所有列，成本可能远高于按需读取。"
                ),
                suggestion="在子查询和最终 SELECT 中只列出业务需要的列。",
            )
        )
    return findings


# --------------------------------------------------------------------- #
# Readability rules                                                     #
# --------------------------------------------------------------------- #


def rule_readability(parsed: ParsedSQL) -> List[Finding]:
    findings: List[Finding] = []
    text = parsed.raw or ""
    if not text:
        return findings

    # Repeated CASE WHEN patterns (rough heuristic: same THEN ... appearing twice).
    case_patterns = re.findall(r"case\s+when[\s\S]*?end", text, re.IGNORECASE)
    if len(case_patterns) >= 3:
        findings.append(
            Finding(
                rule_id="R001",
                category=CATEGORY_READABILITY,
                risk_level="low",
                title=f"出现 {len(case_patterns)} 段 CASE WHEN，建议抽取公共逻辑",
                evidence=_evidence("多段 CASE WHEN ... END"),
                why="重复的 CASE WHEN 容易出现口径不一致的维护风险。",
                suggestion="抽取为公共子查询 / CTE，或使用 MAP / 维度表。",
            )
        )

    # Very long CTEs (heuristic: > 60 lines).
    for cte in parsed.ctes:
        if cte.body.count("\n") >= 60:
            findings.append(
                Finding(
                    rule_id="R002",
                    category=CATEGORY_READABILITY,
                    risk_level="low",
                    title=f"CTE `{cte.name}` 行数较多，建议拆分",
                    evidence=_evidence(cte.name),
                    why="过长的 CTE 难以阅读，也不利于后续口径维护。",
                    suggestion="按业务步骤拆分为多个 CTE 并加上注释。",
                )
            )

    # Missing comments.
    if "--" not in text and "/*" not in text:
        findings.append(
            Finding(
                rule_id="R003",
                category=CATEGORY_READABILITY,
                risk_level="low",
                title="SQL 缺少注释",
                evidence="未发现 -- 或 /* */ 注释。",
                why="缺少注释不利于后续接手与口径审计。",
                suggestion="为关键步骤、口径定义和 JOIN 关系添加注释。",
            )
        )

    # Single-letter aliases for source tables.
    aliases = re.findall(r"\b(?:from|join)\s+\S+\s+(?:as\s+)?([A-Za-z])\b", text, re.IGNORECASE)
    bad_aliases = [a for a in aliases if a.lower() in {"a", "b", "c", "d", "e", "t"}]
    if bad_aliases:
        findings.append(
            Finding(
                rule_id="R004",
                category=CATEGORY_READABILITY,
                risk_level="low",
                title=f"使用了 {len(bad_aliases)} 个单字母别名 ({sorted(set(bad_aliases))})",
                evidence=_evidence("FROM xxx a JOIN yyy b"),
                why="单字母别名可读性差，多表关联时容易看错。",
                suggestion="使用业务含义清晰的短别名，例如 `o` 改为 `order`、`u` 改为 `user`。",
            )
        )
    return findings


# --------------------------------------------------------------------- #
# Date / null rules                                                     #
# --------------------------------------------------------------------- #


def rule_date_boundary(parsed: ParsedSQL) -> List[Finding]:
    findings: List[Finding] = []
    text = parsed.stripped
    lower = text.lower()
    for match in re.finditer(r"between\s+'?(\d{4}-?\d{2}-?\d{2})'?\s+and\s+'?(\d{4}-?\d{2}-?\d{2})'?", lower):
        findings.append(
            Finding(
                rule_id="D001",
                category=CATEGORY_DATE,
                risk_level="medium",
                title="BETWEEN 日期边界需要确认是否双闭区间",
                evidence=_evidence(match.group(0)),
                why=(
                    "BETWEEN 包含两端，与团队习惯使用的 `>= start AND < end` 半开区间不同，"
                    "容易引入多算或少算一天。"
                ),
                suggestion=(
                    "请确认业务期望，必要时改写成 `dt >= 'start' AND dt < 'next_day'`；"
                    "并在验数 SQL 中校对边界天的行数。"
                ),
                needs_confirmation=True,
            )
        )
    return findings


# --------------------------------------------------------------------- #
# Public entry point                                                    #
# --------------------------------------------------------------------- #


def run_all_rules(
    parsed: ParsedSQL,
    tables: List[TableReference],
    joins: List[JoinClause],
    metrics: List[Metric],
) -> List[Finding]:
    """Run every rule and return all findings."""

    findings: List[Finding] = []
    findings.extend(rule_missing_partition_filter(parsed, tables))
    findings.extend(rule_long_partition_window(parsed))
    findings.extend(rule_join_safety(parsed, joins))
    findings.extend(rule_metric_definition(metrics))
    findings.extend(rule_insert_overwrite(parsed))
    findings.extend(rule_performance(parsed, joins, metrics))
    findings.extend(rule_readability(parsed))
    findings.extend(rule_date_boundary(parsed))
    return findings
