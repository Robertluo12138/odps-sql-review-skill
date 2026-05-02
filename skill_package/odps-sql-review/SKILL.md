---
name: odps-sql-review
description: Review ODPS / MaxCompute SQL for production risks (missing partition filters, LEFT JOIN invalidation, join row explosion, INSERT OVERWRITE risk, metric duplication) and performance optimization (COUNT DISTINCT, MAU / YAU long-period distinct user, GROUP BY / JOIN / ROW_NUMBER skew, dynamic partition, MAPJOIN / DISTMAPJOIN / SKEWJOIN, reducer / joiner tuning). Outputs a Chinese 8-section risk review report and validation SQL templates.
license: MIT
compatibility: Portable file-based Agent Skill. Works in any AI tool that can read SKILL.md from a folder (e.g. QoderWork). No Python or external connection required.
metadata:
  version: "0.2.0"
  language: "zh-CN"
  domain: "ODPS MaxCompute SQL review"
  audience: "Data analysts and data engineers releasing ODPS SQL to production"
---

# ODPS / MaxCompute SQL Review Skill

This is a **pure file-based Agent Skill**. The whole skill is the
folder you are reading right now (`SKILL.md`, `references/`,
`examples/`, `templates/`). No installation, no external service, no
Python is required to use it.

---

## 1. What this skill is

A portable ODPS / MaxCompute SQL review skill for:

- production-risk review (correctness, partition safety, JOIN safety,
  metric duplication, INSERT OVERWRITE risk);
- performance optimization (COUNT DISTINCT, MAU / YAU and other
  long-period distinct-user metrics, GROUP BY / JOIN / ROW_NUMBER
  skew, dynamic partitions, MAPJOIN / DISTMAPJOIN / SKEWJOIN
  candidates, reducer / joiner parallelism).

The output is a Chinese 8-section risk review report plus
copy-pasteable validation (验数) SQL templates. The skill is meant for
data analysts and data engineers at Chinese internet companies who
need to verify ODPS SQL before releasing it to production.

## 2. When to use this skill

Use it when the user is:

- about to release a scheduled ODPS task (DataWorks, Airflow, cron);
- about to release a BI report SQL;
- about to release a dataset SQL;
- running an ad-hoc SQL that scans large tables;
- computing MAU / YAU / 30-day / 365-day distinct-user metrics;
- checking partition filters, joins, COUNT DISTINCT, GROUP BY,
  ROW_NUMBER, dynamic partitions, or any ODPS performance risk.

If the user just asks "is this SQL syntactically valid", that is **not**
the use case. This skill is about correctness, safety, and performance
review of SQL that already runs.

## 3. What input this skill supports

The user can provide any of the following (in order of usefulness):

1. **The SQL itself** — pasted text or the contents of a `.sql` file.
   This is the only required input.
2. **Optional table profile** — partition columns, grain, unique keys,
   size level, table type. Use the schema in
   `templates/table_profile_template.yml`.
3. **Optional LogView summary** — total runtime, slowest stage,
   reducer/joiner count, max/avg runtime, whether long-tail is
   present. Use the format in `templates/logview_template.md`.
4. **Optional business context** — target table grain, expected
   primary key, metric definitions, whether approximate distinct is
   acceptable, whether this is a scheduled task or ad-hoc.

If something is missing, do **not** invent it; mark the related
finding as `需要确认`.

## 4. Safety rules — non-negotiable

The agent using this skill **must**:

- **never** connect to ODPS / MaxCompute;
- **never** execute the user's SQL;
- **never** submit jobs;
- **never** modify production tables;
- **never** invent table grain;
- **never** invent unique keys;
- **never** invent table size or partition columns;
- mark every unknown piece of information with `需要确认`;
- **always** output an explicit risk level (高 / 中 / 低);
- **never** answer with only "looks good" — always provide the
  structured 8-section report.

If the user asks the agent to run the SQL or connect to ODPS, the
agent must refuse and explain why (no execution capability is part of
this skill).

## 5. Review workflow the agent must follow

For every SQL the user submits, the agent must:

1. **Read the SQL carefully** before writing anything.
2. **Identify every source table** and check whether it has a
   partition filter (`dt`/`ds`/`pt`/`hh`/`bizdate`/`stat_dt`/…).
   Wrapped expressions like `substr(dt, 1, 6)` lose partition
   pruning — flag them. See `references/review_rules.md`.
3. **Identify every JOIN clause** and check:
   - is `LEFT JOIN` invalidated by a `WHERE b.col = ...` filter on
     the right alias?
   - are right-side keys non-unique → row explosion risk?
   - are two fact tables joined directly without dedup?
   - `ON 1=1`, missing `ON`, `OR` in `ON`, `CAST` in `ON`?
   - is there a many-to-many JOIN that could multiply rows?
4. **Identify every INSERT OVERWRITE PARTITION** clause and check:
   - missing target partition;
   - dynamic partition with unbounded count;
   - date variable substitution risk.
5. **Identify every aggregate metric** (SUM / COUNT / COUNT DISTINCT /
   AVG / MIN / MAX / RATIO / CASE WHEN) and explain it using
   `references/metric_rules.md`.
6. **Identify performance risks** using `references/performance_rules.md`:
   - long-period COUNT DISTINCT (MAU / YAU / 30d / 365d);
   - GROUP BY skew (hot keys);
   - JOIN skew;
   - ROW_NUMBER OVER (PARTITION BY …) skew;
   - dynamic partition risk;
   - MAPJOIN / DISTMAPJOIN / SKEWJOIN candidates;
   - reducer / joiner / parallelism tuning suggestions
     (only with LogView; otherwise mark `需要结合 LogView / 数据量确认`);
   - intermediate / rolling table candidates.
7. **Generate validation SQL templates** using
   `references/validation_checklist.md`. The templates must contain
   placeholders like `${bizdate}` or `<右表唯一键>` so the user is
   forced to supply real values rather than running blindly.
8. **Mark every unknown** with `需要确认`. Examples: unknown table
   grain, unknown unique key, unknown table size, unknown LogView,
   unclear business meaning of a metric.
9. **Output the report** in the exact structure described in
   `references/output_template.md` (also reproduced below).
10. **Never** end with only "looks good". The report must always
    include the risk level and the items still needing confirmation.

## 6. Required output format

Every response must follow this exact Chinese Markdown structure.
Section titles and section numbers must match.

```markdown
# SQL 风险审查报告

## 0. 总体结论
- 总风险等级：高 / 中 / 低
- 是否建议直接上线：是 / 否
- 主要阻塞点：
  - <列出 high+blocking 的问题，没有则写 “暂无明确阻塞点”>
- 主要性能瓶颈：
  - <列出关键性能问题，没有则写 “暂未发现典型性能瓶颈，仍建议结合 LogView / 数据量复核”>
- 需要确认项：
  - <列出影响判断的未知信息>

## 1. 高风险问题
（每条包含：风险点 / 证据 SQL 片段 / 为什么危险 / 建议修改方式 / 是否阻塞上线）

## 2. 中风险问题
（每条包含：风险点 / 证据 SQL 片段 / 影响 / 建议修改方式）

## 3. 低风险 / 可读性问题
（SELECT * / 单字母别名 / 重复 CASE WHEN / 缺少注释 / 过长 CTE / 命名不清等）

## 4. 性能优化建议
分组输出（缺项就写 “暂无明显问题，仍需结合 LogView / 数据量确认”）：
- 分区裁剪
- Join 优化
- Group By / Count Distinct 优化
- 年活 / 月活 / 长周期去重优化
- 动态分区优化
- Reducer / Joiner / 并行度建议
- 中间表 / 滚动表建议

## 5. 指标口径解释
按指标列出（每个指标都包含）：
- 指标名称
- 分子
- 分母
- 过滤条件
- GROUP BY 粒度
- 是否存在重复计算风险
- 是否需要业务确认

## 6. 建议改写 SQL
- 在能确定时给出可执行的改写片段；
- 表粒度 / 唯一键 / 字段语义未知时只给出 “候选改写方向”，不要假装知道答案。

## 7. 上线前验数 SQL
至少包含：
- 源分区行数检查；
- 目标行数检查；
- 主键重复检查；
- 核心字段空值检查；
- JOIN 前后行数对比；
- 昨天 / 今天指标波动检查；
- 分区数据量检查；
- COUNT DISTINCT 校验；
- 右表关联键唯一性检查。

## 8. 需要我补充的信息
仅列影响判断的项目：
- 表分区列；
- 表粒度；
- 唯一键；
- 表大小级别；
- fact / dim 标识；
- 是否允许近似去重；
- 目标表主键；
- 是否调度任务；
- LogView 数据。
```

> **不要盲目推荐固定的 ODPS SET 数值。**
> 如果缺少 LogView / 数据量 / 资源配额信息，必须写
> `需要结合 LogView / 数据量确认`。
> 你可以给候选参数与改写方向，但要说明它们何时适用。

## 7. Files in this skill

```
SKILL.md                              <- you are here
references/
    review_rules.md                   <- correctness rule catalogue
    performance_rules.md              <- performance rule catalogue
    metric_rules.md                   <- how to explain each metric
    validation_checklist.md           <- 验数 SQL checklist
    output_template.md                <- the exact report template
examples/
    bad_left_join.sql                 <- LEFT JOIN invalidated by WHERE
    bad_count_distinct.sql            <- 365-day COUNT DISTINCT on raw table
    bad_no_partition.sql              <- missing partition filter
    bad_join_explosion.sql            <- two fact tables joined w/o dedup
    bad_dynamic_partition.sql         <- INSERT OVERWRITE dynamic partition
    expected_report.md                <- example of the final report shape
templates/
    table_profile_template.yml        <- ask the user to fill this when needed
    logview_template.md               <- ask the user to paste LogView this way
    review_prompt.md                  <- the prompt the user can paste in QoderWork
```

When you need a rule reference, open the matching file under
`references/`. When you need to ask the user for table metadata or
LogView, point them at the matching template under `templates/`.

## 8. How to invoke this skill

Once this skill folder is imported into your AI tool (for example
QoderWork), the user can simply paste their SQL and ask:

> Use the `odps-sql-review` skill to review this SQL.

The agent should then read this `SKILL.md`, follow the workflow in
section 5, and produce the report defined in section 6.

If the user wants a faster shortcut, they can paste the prompt in
`templates/review_prompt.md` together with their SQL.
