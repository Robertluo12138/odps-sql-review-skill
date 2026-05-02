---
name: odps-sql-review
description: Review ODPS / MaxCompute SQL for production risks, correctness issues, join row explosion, metric ambiguity, and performance problems such as COUNT DISTINCT, GROUP BY skew, JOIN skew, ROW_NUMBER skew, dynamic partition skew, MAPJOIN, SKEWJOIN, reducer/joiner tuning, and long-period MAU/YAU distinct-user optimization.
license: MIT
compatibility: Portable file-based Agent Skill. Works best with AI CLIs that can read SKILL.md and run local Python scripts. Also works as a standalone Python CLI without any AI agent.
metadata:
  version: "0.1.0"
  language: "zh-CN"
  domain: "ODPS MaxCompute SQL review"
---

# ODPS / MaxCompute SQL Review Skill

A portable skill for statically reviewing ODPS / MaxCompute SQL before
the query goes to production (scheduled task, BI report, dataset
publication, ad-hoc analysis).

This skill is **CLI agnostic**: it does not depend on any specific AI
CLI, does not write to `~/.claude/skills`, and can be used either as
a file-based agent skill or as a plain Python CLI from a normal
terminal.  The repository root is the skill package; `SKILL.md` lives
at the root for maximum compatibility.

## When to use this skill

- You are about to release a scheduled ODPS task (DataWorks, Airflow,
  cron) and want to catch correctness / performance issues before
  publishing.
- You are publishing a BI report, dataset, or ad-hoc analysis based on
  an ODPS SQL.
- The SQL is syntactically correct but you suspect it may be slow or
  unsafe (LEFT JOIN that may degrade to INNER JOIN, missing partition
  filter, COUNT DISTINCT on long periods, …).
- You need to compute MAU / YAU / 30-day / 365-day distinct user
  counts and want to confirm the design.
- You are reviewing JOIN safety, partition pruning, GROUP BY skew,
  ROW_NUMBER skew, dynamic partition risks, or reducer / joiner
  tuning.

## What input is supported

- A path to a `.sql` file.
- Pasted SQL text (the agent can save it to a temp file and feed it
  to the CLI, or read it directly).
- Optional `table_profile.yml` describing each table's partition
  columns, grain, unique keys, size level and table type.
- Optional LogView summary (paste the fields listed in
  `references/logview_template.md`).
- Optional business context: target table grain, expected primary key,
  metric definitions, whether approximate distinct is acceptable.

## How an AI agent should use this skill

1. **Read the SQL** the user provided (file path or inline text).
2. **Run the static check** if the environment allows it:
   ```bash
   python -m odps_sql_review path/to/query.sql --format markdown
   ```
   When a table profile is available, pass it:
   ```bash
   python -m odps_sql_review path/to/query.sql \
       --table-profile path/to/table_profile.yml
   ```
3. **Read the rule documents** under `references/`:
   - `review_rules.md` for correctness rules.
   - `odps_performance_rules.md` for performance rules.
4. **Review the SQL manually** using the rules.  Combine the static
   findings with your judgment - the static check is conservative
   and may miss business-specific risks.
5. **Identify correctness risks** (partition, JOIN, metric duplication,
   INSERT OVERWRITE, date boundary).
6. **Identify performance risks** (COUNT DISTINCT, GROUP BY/JOIN/
   ROW_NUMBER skew, MAPJOIN candidates, dynamic partition risks,
   long-period MAU/YAU optimization).
7. **Explain every metric** in the SELECT list (numerator, denominator,
   filters, grain, duplication risk, business confirmation needs).
8. **Generate validation SQL templates** so the user can run them in
   ODPS Studio / DataWorks before publishing.
9. **Mark unknowns explicitly with `需要确认`** rather than guessing.
10. **Never say only "looks good"** - always include the explicit risk
    level and the items that still need user confirmation.

## Required report format

The agent must always output the following Chinese Markdown
structure.  The Python CLI also produces this structure when
`--format markdown` is used.

```markdown
# SQL 风险审查报告

## 0. 总体结论
- 总风险等级：高 / 中 / 低
- 是否建议直接上线：是 / 否
- 主要阻塞点：
- 主要性能瓶颈：
- 需要确认项：

## 1. 高风险问题
（每条包含：风险点 / 证据 SQL 片段 / 为什么危险 / 建议修改方式 / 是否阻塞上线）

## 2. 中风险问题
（每条包含：风险点 / 证据 SQL 片段 / 影响 / 建议修改方式）

## 3. 低风险 / 可读性问题
- SELECT *
- 不清晰的别名
- 重复 CASE WHEN
- 缺少注释
- 过长 CTE
- 命名不清

## 4. 性能优化建议
- 分区裁剪
- Join 优化
- Group By / Count Distinct 优化
- 年活 / 月活 / 长周期去重优化
- 动态分区优化
- Reducer / Joiner / 并行度建议
- 中间表 / 滚动表建议

## 5. 指标口径解释
（按指标列出：名称 / 分子 / 分母 / 过滤 / GROUP BY 粒度 / 重复计算风险 / 是否需要业务确认）

## 6. 建议改写 SQL
（不确定时给候选改写方向，不要强行改写）

## 7. 上线前验数 SQL
（源分区行数、目标行数、主键唯一、空值、JOIN 前后行数、波动、分区数据量、COUNT DISTINCT 校验、右表关联键唯一）

## 8. 需要我补充的信息
（仅列影响判断的项目：表分区列、表粒度、唯一键、表大小、fact/dim、是否允许近似、目标主键、是否调度、LogView 信息）
```

> 不要盲目推荐固定的 ODPS SET 数值。如果缺少数据量、LogView 或资源
> 配额信息，请明确写 “需要结合 LogView / 数据量确认”。可以给候选
> 参数与改写方向，但要说明何时适用。

## Workflow snippets the agent can run

```bash
# Full static review in Markdown
python -m odps_sql_review path/to/query.sql --format markdown

# Full static review in JSON (for further processing)
python -m odps_sql_review path/to/query.sql --format json

# With a table profile (recommended)
python -m odps_sql_review path/to/query.sql \
    --table-profile path/to/table_profile.yml

# Only output the validation SQL templates
python -m odps_sql_review path/to/query.sql --generate-validation-sql

# Focus on correctness only
python -m odps_sql_review path/to/query.sql --focus correctness

# Focus on performance only
python -m odps_sql_review path/to/query.sql --focus performance

# Read SQL from stdin
cat query.sql | python -m odps_sql_review -

# Compatibility wrapper for agents that look for scripts/
python scripts/sql_static_check.py --input path/to/query.sql --format json
```

## Risk rules reference

The full rule set is documented in:

- `references/review_rules.md` - high / medium / low risk catalogue.
- `references/odps_performance_rules.md` - performance rules grouped
  by topic.
- `references/output_template.md` - the exact Markdown template the
  report must follow.
- `references/logview_template.md` - what to paste from LogView for
  deeper performance diagnosis.
- `references/troubleshooting.md` - common questions.

## Hard safety rules

- This skill never connects to ODPS.
- This skill never executes user SQL.
- This skill never submits jobs.
- This skill never modifies database tables.
- All output is static analysis: review reports and validation SQL
  templates that the user is expected to run themselves.
