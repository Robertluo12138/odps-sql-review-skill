# ODPS / MaxCompute SQL 评审规则手册

> 本文是 odps-sql-review 静态分析与 Agent 提示词共同遵循的规则手册。
> 所有规则按风险等级分为高 / 中 / 低三档，目标读者是国内互联网公司
> 数据分析师 / 数据开发同学。

风险等级判定原则：

- **高风险**：上线后可能引发数据事故（写错分区、行数膨胀、口径错误、
  SQL 跑挂主任务）。建议在评审中显式标记 “阻塞上线”，必须修复。
- **中风险**：性能瓶颈、口径不清晰、潜在重复计算、长尾任务。建议
  在上线前修复，至少需要业务确认。
- **低风险**：可读性、注释、命名、SELECT * 等。建议安排在迭代中
  优化，不阻塞上线。

## 一、高风险规则

### 1. 缺失分区过滤（P001）
- 触发条件：`FROM` / `JOIN` 引用一张物理表，但在该表的子查询或
  WHERE 中找不到 `dt`/`ds`/`pt`/`hh`/`bizdate`/`stat_dt` 等分区列
  的等值或区间过滤；并且也未在内层做 partition 子查询过滤。
- 为什么危险：ODPS 按分区列存储，无分区过滤即全表扫描，可能产生
  TB 级 IO，被资源队列限流甚至直接失败。
- 建议修复：
  - 在最内层子查询 `WHERE` 中过滤分区列；
  - 不要依赖最外层 WHERE 过滤；
  - 维度小表请通过 `table_profile.yml` 显式声明 `size_level: small`，
    并在评审报告中说明无分区。

### 2. 分区列被函数包裹（P002）
- 例：`WHERE substr(dt, 1, 6) = '202504'`。
- 为什么危险：分区裁剪失效，会扫描所有分区。
- 建议修复：改写为 `dt BETWEEN '20250401' AND '20250430'` 或先
  在外层做 `month_id = '202504'` 这类预生成字段。

### 3. LEFT JOIN 被 WHERE 退化（J005）
- 例：
  ```sql
  SELECT ...
  FROM   a
  LEFT JOIN b ON a.id = b.id
  WHERE  b.status = 1
  ```
  右表字段被 WHERE 直接过滤后，LEFT JOIN 变成 INNER JOIN。
- 为什么危险：原本期望保留 a 的全部行，结果丢失了 a 中右表为空
  的行，业务口径出现偏差。
- 建议修复：把 `b.status = 1` 移到 ON 子句，或先在子查询中
  过滤右表，再 LEFT JOIN。

### 4. JOIN 行数膨胀（J001/J002/J006/J010）
- 触发条件：右表关联键不唯一、JOIN 关联条件包含 `OR`、`ON 1=1`、
  缺少 ON、JOIN 多张事实表、关联字段类型不一致需要 CAST。
- 为什么危险：行数膨胀直接导致下游 SUM/COUNT 重复计算，并且任务
  数据量爆炸。
- 建议修复：
  - 在 `table_profile.yml` 中声明右表 `unique_keys`；
  - 必要时在 JOIN 前先 ROW_NUMBER 去重；
  - 用 `IN (...)` 或 `EXISTS` 代替 OR；
  - 关联键类型保持一致，避免 CAST 隐式过滤。

### 5. INSERT OVERWRITE 分区风险（I001/I002）
- 触发条件：未指定目标分区、动态分区数量不可控、调度日期变量
  替换错误。
- 为什么危险：可能覆盖整张表、写入错误分区，且 ODPS 不可恢复。
- 建议修复：
  - 显式 `PARTITION (dt='${bizdate}')`；
  - 动态分区前先估算分区数量；
  - 写入完成后增加目标分区行数与主键校验。

### 6. 指标重复计算（M005 等）
- 触发条件：JOIN 关联键不唯一时直接 SUM / COUNT；多个 COUNT DISTINCT
  混在一个查询；不同粒度指标放在同一个 GROUP BY。
- 为什么危险：报表指标错误，且不易发现。
- 建议修复：先在最小粒度去重，再聚合；将不同粒度指标拆为多个
  查询。

### 7. 长周期 + COUNT DISTINCT 组合（PF006）
- 例：`SELECT COUNT(DISTINCT user_id) FROM dwd_xxx WHERE dt BETWEEN '20240101' AND '20241231'`。
- 为什么危险：单 reducer 全局去重，且数据量为 365 天的明细，几乎
  必然超时。
- 建议修复：
  - 先建立用户日活中间表 `dws_xxx_user_active_di`（dt + user_id）；
  - 在该表上做 30/365 天去重；
  - 可接受近似时使用 `approx_distinct`，并在文档中注明；
  - 严格精确指标禁止把日 UV 累加为 MAU。

## 二、中风险规则

### 1. 多 COUNT DISTINCT（M005 / PF005）
- 同一查询出现两个及以上 COUNT DISTINCT。
- 建议：拆分为多个查询后 UNION，或先在用户粒度去重再聚合。

### 2. GROUP BY / ROW_NUMBER 倾斜（PF001 / PF002）
- 触发条件：出现 GROUP BY、ROW_NUMBER OVER (PARTITION BY ...)。
- 建议：结合 LogView 判断是否倾斜，必要时使用二阶段聚合或
  `SET odps.sql.groupby.skewindata=true`。
- 不能盲目设置 SET 值，必须有 LogView 数据支撑。

### 3. BETWEEN 日期边界（D001）
- 触发条件：`BETWEEN 'start' AND 'end'`。
- 建议：确认是否双闭区间，必要时改成 `>= 'start' AND < 'next_day'`。

### 4. SELECT *（PF008）
- 建议：只 SELECT 业务真正使用的列，降低 IO。

### 5. 大表 JOIN 大表未预过滤（PF004）
- 建议：先在子查询里过滤分区与字段，必要时考虑 SKEW JOIN。

### 6. ORDER BY 没有 LIMIT（PF007）
- 建议：补 LIMIT，或改用 DISTRIBUTE BY + SORT BY、ROW_NUMBER。

## 三、低风险规则

- **R001**：多段 CASE WHEN，建议抽取公共逻辑；
- **R002**：CTE 行数过长，建议拆分；
- **R003**：缺少注释；
- **R004**：单字母别名 `a`/`b`/`t`，可读性差；
- **PF003**：维度表可能可以使用 MAPJOIN（仍需确认大小）。

## 四、规则约定

- 所有 “是否需要确认” 的规则都标记 `needs_confirmation=True`，
  报告中的 “需要我补充的信息” 章节会列出待确认项。
- 在不确定 unique_keys / 表粒度 / 表大小 时，禁止给出绝对结论。
- 性能 SET 参数（如 `odps.sql.skewjoin`、`odps.stage.reducer.num`）
  必须强调 “需要结合 LogView / 数据量确认”。
