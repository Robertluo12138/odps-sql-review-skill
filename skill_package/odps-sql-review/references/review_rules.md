# 正确性 / 生产风险规则手册（review_rules）

> 本文是 odps-sql-review 评审中关于 **正确性与生产风险** 的规则集合。
> 所有规则按风险等级分为 **高 / 中 / 低** 三档，目标读者是国内
> 互联网公司数据分析师 / 数据开发同学。
>
> 风险等级判定原则：
> - **高风险**：上线后可能引发数据事故（写错分区、行数膨胀、
>   口径错误、主任务跑挂），必须修复，建议显式标记 “阻塞上线”；
> - **中风险**：性能瓶颈、口径不清晰、潜在重复计算、长尾任务，
>   上线前应修复，至少需要业务确认；
> - **低风险**：可读性 / 命名 / 注释类，安排在迭代中优化即可，
>   不阻塞上线。

性能与并行度类的规则放在 `performance_rules.md`，
指标口径解释放在 `metric_rules.md`，
验数 SQL 放在 `validation_checklist.md`。

---

## 一、高风险规则

### R-H1. 缺失分区过滤
- **触发条件**：`FROM` / `JOIN` 引用一张物理表，但找不到 `dt`/`ds`/`pt`/`hh`/`bizdate`/`stat_dt`/`log_dt`/`month_id`/`year_id` 等分区列的等值或区间过滤；CTE 内层也没有过滤。
- **为什么危险**：ODPS 按分区列存储，未带分区过滤会触发全表扫描，
  可能产生 TB 级 IO，被资源队列限流甚至直接失败。
- **修复建议**：
  - 在最内层子查询的 `WHERE` 中过滤分区列；
  - 不要依赖最外层 WHERE 过滤；
  - 维度小表请通过 `templates/table_profile_template.yml` 显式
    声明 `size_level: small`，并在评审报告中说明为什么不需要分区。
- **报告字段**：风险点 / 证据 / 为什么危险 / 建议修改方式 / 是否阻塞上线 = 是。

### R-H2. 分区列被函数包裹
- **触发条件**：分区列出现在 `substr` / `cast` / `date_format` /
  `from_unixtime` 等函数内。
- **示例**：`WHERE substr(dt, 1, 6) = '202504'`。
- **为什么危险**：partition pruning 失效，ODPS 优化器无法识别分区
  谓词，会扫描所有分区。
- **修复建议**：改写为对分区列直接做等值或区间比较，例如
  `dt BETWEEN '20250401' AND '20250430'`，将月份截断逻辑放到外层。

### R-H3. LEFT JOIN 被 WHERE 退化为 INNER JOIN
- **触发条件**：`LEFT JOIN b ON a.id = b.id` 后，`WHERE` 中出现
  `b.col = ...` / `b.col > ...` / `b.col IN (...)` / `b.col LIKE ...`
  之类的过滤（`b.col IS NULL` 不算，因为是 anti-join 写法）。
- **示例**：
  ```sql
  SELECT ...
  FROM   a
  LEFT JOIN b ON a.id = b.id
  WHERE  b.status = 1   -- 这里把 LEFT JOIN 退化为 INNER JOIN
  ```
- **为什么危险**：原本期望保留 a 的全部行，结果丢失了 a 中右表为
  空的行，业务口径出现偏差。
- **修复建议**：
  - 把 `b.status = 1` 移到 `ON` 子句；或
  - 在子查询里先过滤右表，再 `LEFT JOIN`：
    ```sql
    LEFT JOIN (SELECT * FROM b WHERE status = 1) b ON a.id = b.id
    ```
- **要在报告里说清业务差异**：保留所有左表 vs 仅保留有匹配的左表。

### R-H4. JOIN 行数膨胀
- **触发条件**（任一）：
  - 右表关联键不唯一；
  - 维度表未去重；
  - 多张事实表直接 JOIN（订单 JOIN 支付、订单 JOIN 物流等）；
  - 关联键含 NULL；
  - `ON` 条件包含 `OR`；
  - `ON 1=1`；
  - JOIN 缺少 `ON`；
  - 关联键类型不一致需要 `CAST`。
- **为什么危险**：行数膨胀直接导致下游 `SUM` / `COUNT` 重复计算，
  且任务数据量爆炸。
- **修复建议**：
  - 在 `table_profile.yml` 中声明右表 `unique_keys`；
  - 必要时在 JOIN 前先 `ROW_NUMBER` 去重 / 预聚合；
  - 用 `IN (...)` 或 `EXISTS` 代替 `OR`；
  - 关联键类型保持一致，避免 `CAST` 隐式过滤。
- **务必在验数 SQL 里包含**：右表关联键重复检查 + JOIN 前后行数对比。

### R-H5. INSERT OVERWRITE 分区风险
- **触发条件**：
  - 未指定目标分区（会覆盖整张表）；
  - 动态分区数量不可控；
  - 调度日期变量（`${bizdate}` / `${yyyymmdd}`）替换错误。
- **为什么危险**：在 ODPS 上几乎不可恢复，是最常见的线上事故来源。
- **修复建议**：
  - 显式 `PARTITION (dt = '${bizdate}')`；
  - 动态分区前先评估分区数量，必要时拆分为静态分区多次写入；
  - 调度系统中确认变量定义；
  - 写入完成后增加目标分区行数与主键校验。

### R-H6. 指标重复计算（来自 JOIN）
- **触发条件**：
  - JOIN 关联键不唯一时直接 `SUM` / `COUNT`；
  - 多个 `COUNT DISTINCT` 混在一个查询；
  - 不同粒度指标放在同一个 `GROUP BY`。
- **为什么危险**：报表指标错误，且不易发现。
- **修复建议**：
  - 先在最小粒度去重，再聚合；
  - 将不同粒度指标拆为多个查询；
  - 在指标口径解释（第 5 节）里说明分子 / 分母 / 粒度。

### R-H7. 长周期 + COUNT DISTINCT 组合
- **触发条件**：原始明细表（如 `dwd_xxx_user_action_di`）上同时
  出现长窗口扫描（30 / 365 天）与 `COUNT DISTINCT user_id`。
- **为什么危险**：单 reducer 全局去重 + 长窗口明细扫描，几乎必然
  超时；典型导致主任务挂掉。
- **修复建议**：
  - 优先落地按天聚合的中间表 `dws_xxx_user_active_di`（dt + user_id）；
  - 在该表上做 30 / 365 天去重；
  - 可接受近似时使用 `approx_distinct`，并在文档中注明 “非精确”；
  - **严格精确财务指标禁止把日 UV 累加为 MAU / YAU**。
- **细节见**：`performance_rules.md` 中的 “MAU / YAU 长周期去重优化”。

---

## 二、中风险规则

### R-M1. 多 COUNT DISTINCT
- 同一查询出现两个及以上 `COUNT DISTINCT`。
- **建议**：拆分为多个查询后 `UNION ALL`，或先在用户粒度去重再聚合。

### R-M2. BETWEEN 日期边界
- `BETWEEN 'start' AND 'end'` 是双闭区间。
- **建议**：确认是否双闭，必要时改成 `>= 'start' AND < 'next_day'`。

### R-M3. SELECT *
- 列存表上 `SELECT *` 会读取所有列，IO 成本可能远高于按需读取。
- **建议**：在子查询和最终 SELECT 中只列出业务需要的列。

### R-M4. ORDER BY 没有 LIMIT
- ODPS 中 `ORDER BY` 是单 reducer 全局排序，没有 LIMIT 会非常慢
  甚至 OOM。
- **建议**：补 LIMIT，或改用 `DISTRIBUTE BY` + `SORT BY` / `ROW_NUMBER`。

### R-M5. 大表 JOIN 大表未预过滤
- 两张大表直接 JOIN 会产生大 shuffle，且热点 key 容易造成 SKEW JOIN。
- **建议**：
  - 在子查询里先过滤分区与字段；
  - 已知热点 key 时考虑 `SET odps.sql.skewjoin=true` 或手工拆分；
  - 需要 LogView / 数据量确认。

### R-M6. GROUP BY 粒度与输出字段不一致
- `GROUP BY` 只列出部分维度，但 SELECT 里又有不在 GROUP BY 的非聚合
  字段（ODPS 在某些情况下不报错但语义不明）。
- **建议**：补齐 GROUP BY，或对额外字段加聚合函数，或改用窗口函数。

### R-M7. 函数包裹被过滤列
- 在被过滤列上使用函数会失去索引 / 分区裁剪。
- **建议**：把函数移到等号右侧，或者使用预生成字段。

---

## 三、低风险 / 可读性规则

- **R-L1. SELECT \***：可读性 + 性能均差。
- **R-L2. 单字母别名**：`a`/`b`/`t`/`c`，多表关联时容易看错。
- **R-L3. 重复 CASE WHEN**：抽取公共逻辑或使用 MAP / 维度表。
- **R-L4. 缺少注释**：不利于后续接手与口径审计。
- **R-L5. 过长 CTE**：建议按业务步骤拆分，每个 CTE 30 行以内。
- **R-L6. 命名不清**：表别名 `t1`/`t2`，列别名 `c1`/`c2`，
  指标别名 `cnt`/`amt`，建议带业务含义。

---

## 四、规则使用约定

1. 当评审 SQL 缺少 `table_profile` 时，**禁止猜测唯一键 / 表粒度**，
   必须把相关结论标记为 `需要确认`。
2. 性能 SET 参数（如 `odps.sql.skewjoin`、`odps.stage.reducer.num`）
   必须强调 “需要结合 LogView / 数据量确认”，不要给出固定值。
3. 报告中的每个 finding 都要给出可操作的修改建议，禁止只描述问题。
4. 对于无法判断的项目，写明 “需要确认 + 期望用户提供什么信息”。
