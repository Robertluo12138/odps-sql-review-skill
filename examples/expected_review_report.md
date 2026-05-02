# SQL 风险审查报告

## 0. 总体结论
- 总风险等级：高
- 是否建议直接上线：否
- 主要阻塞点：
  - JOIN 风险：`dim_xxx_shop_df`
- 主要性能瓶颈：
  - 维度表 `dim_xxx_shop_df` 可能适合 MAPJOIN
- 需要确认项：
  - `dwd_xxx_order_di` 的分区列、表粒度、唯一键、表大小级别（fact/dim、small/large）。
  - `dim_xxx_shop_df` 的分区列、表粒度、唯一键、表大小级别（fact/dim、small/large）。
  - 目标表的主键、是否调度任务、调度日期变量。
  - LogView：总耗时、慢 stage 类型、reducer/joiner 数量、最大/平均运行时间、是否长尾。

## 1. 高风险问题
#### JOIN 风险：`dim_xxx_shop_df`
- 风险点：JOIN 风险：`dim_xxx_shop_df`
- 证据 SQL 片段：
  ```sql
  LEFT JOIN dim_xxx_shop_df b ON a.shop_id = b.shop_id
  ```
- 为什么危险：LEFT/RIGHT JOIN 的右表别名 `b` 被 WHERE 直接过滤，可能让外连接退化为 INNER JOIN，请将条件移到 ON 子句或子查询里。
- 建议修改方式：请明确两侧的关联粒度、是否需要去重、是否需要把过滤放到子查询。对于必须保留左表全部行的需求，过滤条件应放在 ON 子句或写成右表子查询。
- 是否阻塞上线：是
- 静态分析判定：需要业务/数据确认


## 2. 中风险问题
- 暂未发现中风险问题。


## 3. 低风险 / 可读性问题
#### 确认目标分区变量是否正确
- 风险点：确认目标分区变量是否正确
- 证据 SQL 片段：
  ```sql
  partition (dt = '${bizdate}')
  ```
- 为什么危险：调度任务里日期变量替换错误可能写入到错误的分区。
- 建议修改方式：请核实 ${bizdate} / ${yyyymmdd} 等变量是否与调度系统对齐；建议在验数 SQL 中检查目标分区的行数与基础指标。
- 静态分析判定：需要业务/数据确认

#### 维度表 `dim_xxx_shop_df` 可能适合 MAPJOIN
- 风险点：维度表 `dim_xxx_shop_df` 可能适合 MAPJOIN
- 证据 SQL 片段：
  ```sql
  LEFT JOIN dim_xxx_shop_df b ON a.shop_id = b.shop_id
  ```
- 为什么危险：维度表通常较小，使用 MAPJOIN 可以避免 shuffle，显著加速 JOIN。
- 建议修改方式：在 SELECT 后加 `/*+ MAPJOIN(b) */`，确认维度表大小可以放入 worker 内存 (默认 512MB) 后再开启；中等大小的表可以评估 DISTMAPJOIN。
- 静态分析判定：需要业务/数据确认

#### 使用了 2 个单字母别名 (['a', 'b'])
- 风险点：使用了 2 个单字母别名 (['a', 'b'])
- 证据 SQL 片段：
  ```sql
  FROM xxx a JOIN yyy b
  ```
- 为什么危险：单字母别名可读性差，多表关联时容易看错。
- 建议修改方式：使用业务含义清晰的短别名，例如 `o` 改为 `order`、`u` 改为 `user`。
- 静态分析判定：可由静态分析判断


## 4. 性能优化建议
### 分区裁剪
- 暂无明显问题（仍需结合 LogView / 数据量确认）。

### Join 优化
- **JOIN 风险：`dim_xxx_shop_df`**
  - 现象：LEFT/RIGHT JOIN 的右表别名 `b` 被 WHERE 直接过滤，可能让外连接退化为 INNER JOIN，请将条件移到 ON 子句或子查询里。
  - 建议：请明确两侧的关联粒度、是否需要去重、是否需要把过滤放到子查询。对于必须保留左表全部行的需求，过滤条件应放在 ON 子句或写成右表子查询。

- **维度表 `dim_xxx_shop_df` 可能适合 MAPJOIN**
  - 现象：维度表通常较小，使用 MAPJOIN 可以避免 shuffle，显著加速 JOIN。
  - 建议：在 SELECT 后加 `/*+ MAPJOIN(b) */`，确认维度表大小可以放入 worker 内存 (默认 512MB) 后再开启；中等大小的表可以评估 DISTMAPJOIN。


### Group By / Count Distinct 优化
- 暂无明显问题（仍需结合 LogView / 数据量确认）。

### 年活 / 月活 / 长周期去重优化
- 暂无明显问题（仍需结合 LogView / 数据量确认）。

### 动态分区优化
- 暂无明显问题（仍需结合 LogView / 数据量确认）。

### Reducer / Joiner / 并行度建议
- 暂无明显问题（仍需结合 LogView / 数据量确认）。

### 中间表 / 滚动表建议
- **中间表 / 滚动表评估**
  - 现象：长周期去重 / 多次复用同一聚合时，中间表能显著降低成本。
  - 建议：若同一聚合被多个报表复用，建议落地 dws_xxx_user_active_di 等中间表；MAU/YAU 等指标考虑用户日活滚动表。需要结合数据量与业务复用度评估。

## 5. 指标口径解释
- 未识别到聚合指标，若 SQL 应当输出指标，请确认 SELECT 列表。


## 6. 建议改写 SQL
由于静态分析无法确认表粒度、唯一键、字段含义，下面只给出候选改写方向，请结合 table_profile.yml 和业务上下文确认后再落地：

- **JOIN 改写**：将右表过滤条件移到子查询中先过滤，再 JOIN；对右表先按 `unique_keys` 做 ROW_NUMBER 去重后再 JOIN，避免行数膨胀。


## 7. 上线前验数 SQL
### 源分区行数
```sql
-- 1. 源表分区行数检查
SELECT COUNT(1) AS row_cnt
FROM   dwd_xxx_order_di
WHERE  dt = '${bizdate}'  -- 请替换为实际分区列与值
;
```
```sql
-- 1. 源表分区行数检查
SELECT COUNT(1) AS row_cnt
FROM   dim_xxx_shop_df
WHERE  dt = '${bizdate}'  -- 请替换为实际分区列与值
;
```

### JOIN 关联键空值
```sql
-- 2. JOIN 关联键空值检查 (a.shop_id, b.shop_id)
SELECT SUM(CASE WHEN a.shop_id IS NULL THEN 1 ELSE 0 END) AS left_null_cnt,
       SUM(CASE WHEN b.shop_id IS NULL THEN 1 ELSE 0 END) AS right_null_cnt
FROM   dim_xxx_shop_df b
WHERE  dt = '${bizdate}'
;
```

### 右表关联键唯一性
```sql
-- 3. 右表 `dim_xxx_shop_df` 关联键重复检查
SELECT b.shop_id, COUNT(1) AS cnt
FROM   dim_xxx_shop_df
WHERE  dt = '${bizdate}'
GROUP BY b.shop_id
HAVING COUNT(1) > 1
LIMIT 50;
```

### JOIN 前后行数膨胀
```sql
-- 4. JOIN 前后行数膨胀对比
-- 4a. JOIN 前左表行数
SELECT COUNT(1) AS left_cnt
FROM   dwd_xxx_order_di
WHERE  dt = '${bizdate}'
;

-- 4b. JOIN 后行数
SELECT COUNT(1) AS joined_cnt
FROM   <在此粘贴你的 JOIN 子查询>
WHERE  dt = '${bizdate}'
;
```

### 目标表主键唯一
```sql
-- 5. 目标表主键重复检查 (写入完成后执行)
SELECT <主键字段>, COUNT(1) AS cnt
FROM   <目标表>
WHERE  dt = '${bizdate}'
GROUP BY <主键字段>
HAVING COUNT(1) > 1
LIMIT 50;
```

### 核心字段空值
```sql
-- 6. 核心字段空值检查
SELECT SUM(CASE WHEN <核心字段> IS NULL THEN 1 ELSE 0 END) AS null_cnt,
       COUNT(1) AS row_cnt
FROM   <目标表>
WHERE  dt = '${bizdate}'
;
```

### 昨日今日指标波动
```sql
-- 7. 昨天 vs 今天指标波动检查
SELECT dt,
       <核心指标>
FROM   <目标表>
WHERE  dt IN ('${bizdate}', '${yyyymmdd_1}')
ORDER BY dt
;
```

### 近 7 天分区数据量
```sql
-- 8. 分区数据量检查 (评估是否异常大或异常小)
SELECT dt, COUNT(1) AS row_cnt
FROM   <目标表>
WHERE  dt BETWEEN '${yyyymmdd_7}' AND '${bizdate}'
GROUP BY dt
ORDER BY dt
;
```

## 8. 需要我补充的信息
- `dwd_xxx_order_di` 的分区列、表粒度、唯一键、表大小级别（fact/dim、small/large）。
- `dim_xxx_shop_df` 的分区列、表粒度、唯一键、表大小级别（fact/dim、small/large）。
- 目标表的主键、是否调度任务、调度日期变量。
- LogView：总耗时、慢 stage 类型、reducer/joiner 数量、最大/平均运行时间、是否长尾。

> 本报告由 odps-sql-review 静态分析生成，未连接 ODPS。最终风险结论需要结合 数据量、LogView、业务口径、调度上下文与 table_profile.yml 复核。
