# 一键评审 Prompt（直接粘到 QoderWork）

> 把下面这段文字 + 你的 SQL 一起发给 AI Agent，它就会按
> `odps-sql-review` skill 的规则输出标准的 8 节评审报告。
>
> **零 profile 模式（默认）**：直接用最简版即可，**只贴 SQL 就够了**。
> 不需要先维护一份完整的表元信息字典。Agent 会先跑确定性的 SQL 文本
> 检查；只有在某个具体结论被卡住时，它才会向你索要那一项最小信息。
>
> 进阶版只是在 **你恰好已经有** table profile / LogView 摘要时才更
> 划算，没有就保持空白，**不要为了填满模板而捏造字段**。

---

## 最简版（推荐，零 profile 默认走这个）

```
Use the `odps-sql-review` skill to review the following ODPS / MaxCompute SQL.

请按 SKILL.md 的工作流程执行，重点检查：
- 缺失分区过滤；
- LEFT JOIN 是否被 WHERE 退化为 INNER JOIN；
- JOIN 行数膨胀风险；
- COUNT DISTINCT 性能；
- MAU / YAU / 长周期去重优化；
- GROUP BY / JOIN / ROW_NUMBER 倾斜；
- 动态分区风险；
- INSERT OVERWRITE 安全；
- 指标口径解释；
- 上线前验数 SQL。

输出必须严格按照 references/output_template.md 的 8 节结构。
不允许只回答 “looks good”。未知信息请写 “需要确认”。

下面是要评审的 SQL：

<在这里粘贴 SQL>
```

---

## 进阶版（仅在你已经有 profile / LogView 时使用）

> 任何字段不知道就**整段删掉或留空**，Agent 会自动标 `需要确认`。
> 不要为了填满字段而编造值。

```
Use the `odps-sql-review` skill to review the following ODPS SQL.

【SQL】
<在这里粘贴 SQL>

【Table profile】（可选 — 没有就删掉这一段）
tables:
  <table_name>:
    partition_cols: ["dt"]
    grain: ["dt", "user_id"]
    unique_keys:
      - ["dt", "user_id"]
    size_level: "large"
    table_type: "fact"
    description: "..."

【LogView 摘要】（可选 — 没有就删掉这一段）
任务总耗时：
最慢 stage：
stage 类型：
输入行数：
reducer / joiner 数：
单 worker 最长耗时：
单 worker 平均耗时：
是否只有少数 worker 慢：

【业务上下文】（可选 — 没有就删掉这一段）
- 目标表主键：
- 是否调度任务：
- 是否允许近似去重：
- 关键指标的业务定义：

请按 SKILL.md 的 8 节模板输出，未知信息标 “需要确认”。
```

---

## 仅检查特定方向

```
Use `odps-sql-review` skill, focus only on <correctness | performance | metrics> for this SQL:

<SQL>
```

可选 focus：
- `correctness`：仅检查正确性 / 生产风险（分区、JOIN、INSERT OVERWRITE、口径）；
- `performance`：仅检查性能优化（COUNT DISTINCT、倾斜、MAPJOIN、并行度）；
- `metrics`：仅做指标口径解释（第 5 节）。

---

## 安全提醒

不要把以下信息粘到任何 AI 工具：

- 真实公司 SQL 中的内部表名、库名、项目名 → 改成 `fake_xxx_*` 后再贴；
- LogView 链接 → 只贴上文 “摘要” 字段；
- AK / SK / token / endpoint；
- 真实用户数据样本。

如果不能脱敏，可在内网环境改用与本 skill 配套的可选离线工具
`odps_sql_review` Python CLI（独立项目，**不属于本 skill 包**，
按需自行获取）。本 skill 本身不依赖该 CLI，QoderWork 用户无需安装。
