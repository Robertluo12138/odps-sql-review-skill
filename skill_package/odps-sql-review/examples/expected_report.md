# SQL 风险审查报告

> 这是 AI Agent 在 `examples/bad_left_join.sql` 上输出的示例报告。
> 真实评审报告需要按 `references/output_template.md` 的结构输出，
> 并根据用户实际提供的 SQL / table profile / LogView 调整内容。

## 0. 总体结论
- 总风险等级：高
- 是否建议直接上线：否
- 主要阻塞点：
  - 高 R-H3：LEFT JOIN 被 WHERE 退化为 INNER JOIN（右表 `b.status = 1` 直接放在 WHERE）
- 主要性能瓶颈：
  - 维度表 `dim_xxx_shop_df` 可能适合 MAPJOIN（需要确认实际大小）
- 需要确认项：
  - `dwd_xxx_order_di` 的分区列、表粒度、唯一键、表大小级别
  - `dim_xxx_shop_df` 的分区列、表粒度、唯一键、表大小级别（fact / dim）
  - 目标表 `ads_xxx_user_order_di` 的主键
  - 业务对 “shop_status = 1” 的定义：是否要保留 “订单关联不到店铺” 的行
  - LogView：总耗时、慢 stage、reducer / joiner 数、是否长尾

## 1. 高风险问题

#### LEFT JOIN 被 WHERE 退化为 INNER JOIN
- 风险点：右表 `dim_xxx_shop_df` 的字段 `b.status` 被直接写在最外层 WHERE，会过滤掉左表 `dwd_xxx_order_di` 中无匹配的订单
- 证据 SQL 片段：
  ```sql
  FROM   dwd_xxx_order_di a
  LEFT JOIN dim_xxx_shop_df b
         ON a.shop_id = b.shop_id
  WHERE  a.dt = '${bizdate}'
    AND  b.status = 1
  ```
- 为什么危险：原本期望保留订单全量行，结果只保留了 `shop_id` 在维度表中且 `status = 1` 的订单；订单关联不到店铺 / 店铺已下线 的订单会被静默丢弃，下游报表的订单数会偏小，业务口径出错
- 建议修改方式：
  - 把过滤移到 ON 子句：
    ```sql
    LEFT JOIN dim_xxx_shop_df b
           ON a.shop_id = b.shop_id
          AND b.status = 1
    ```
  - 或者写成右表子查询后再 JOIN：
    ```sql
    LEFT JOIN (SELECT shop_id, shop_name, status FROM dim_xxx_shop_df WHERE status = 1 AND dt = '${bizdate}') b
           ON a.shop_id = b.shop_id
    ```
- 是否阻塞上线：是

## 2. 中风险问题

#### 维度表 `dim_xxx_shop_df` 缺少独立的 `dt` 过滤
- 风险点：`dim_xxx_shop_df` 看起来是日全量维度表（按 `dt` 分区），但 SQL 仅过滤 `a.dt = '${bizdate}'`，没有显式约束 `b.dt`
- 证据 SQL 片段：
  ```sql
  LEFT JOIN dim_xxx_shop_df b ON a.shop_id = b.shop_id
  WHERE  a.dt = '${bizdate}'
  ```
- 影响：会扫描维度表的全部历史分区，IO 浪费
- 建议修改方式：在 ON 子句或子查询中显式过滤 `b.dt = '${bizdate}'`

## 3. 低风险 / 可读性问题
- 单字母别名 `a` / `b`，多表关联时建议改为有业务含义的短别名（如 `o` / `shop`）
- 缺少注释，下游接手时不清楚 “为什么要 LEFT JOIN”
- SELECT 中包含 `b.status` 但同时写在 WHERE 里，建议明确该字段是 “输出列” 还是 “过滤条件”

## 4. 性能优化建议

### 分区裁剪
- `dwd_xxx_order_di` 已过滤 `a.dt = '${bizdate}'`，✓
- `dim_xxx_shop_df` 未独立过滤 `b.dt`，需补充

### Join 优化
- 维度表 `dim_xxx_shop_df` 通常较小，候选 `/*+ MAPJOIN(b) */`，但实际大小需确认（< 512 MB 才适合）
- 不要同时使用 MAPJOIN 与 SKEW JOIN

### Group By / Count Distinct 优化
- 暂无明显问题，仍需结合 LogView / 数据量确认

### 年活 / 月活 / 长周期去重优化
- 暂无明显问题（本 SQL 是单日订单 + 店铺关联，不涉及长周期去重）

### 动态分区优化
- 本 SQL 使用静态目标分区 `PARTITION (dt = '${bizdate}')`，✓
- 建议在写入完成后增加目标分区行数校验（见第 7 节 V2）

### Reducer / Joiner / 并行度建议
- 没有 LogView，无法判断当前并行度是否合适
- 需要结合 LogView / 数据量确认

### 中间表 / 滚动表建议
- 该 SQL 当前不适合落地中间表（业务粒度 = 订单粒度 + 店铺维度，已在 dwd 层）

## 5. 指标口径解释

#### 指标：`order_amt`
- 表达式：`a.order_amt`
- 聚合类型：PASS_THROUGH（明细输出，不是聚合指标）
- 分子：订单金额原始值
- 分母：非比率指标，无分母
- 过滤条件：`a.dt = '${bizdate}'`，以及 LEFT JOIN 退化后的 `b.status = 1`
- GROUP BY 粒度：订单粒度（无 GROUP BY，直接输出明细）
- 是否存在重复计算风险：是 —— 当前 LEFT JOIN 退化后 + 维度表 shop_id 不唯一时会复制订单行
- 是否需要业务确认：是 —— 是否需要保留 `b.status != 1` 的订单
- 备注：第 1 节高风险修复后，该问题随之消失

#### 指标：`shop_status`
- 表达式：`b.status AS shop_status`
- 聚合类型：PASS_THROUGH
- 分子 / 分母：非聚合指标
- 过滤条件：见上
- 备注：当前 SQL 把 `b.status` 既当输出列又当过滤列，建议拆分语义

## 6. 建议改写 SQL

候选改写方向（**唯一键、维度表大小未确认前不要直接改写为 MAPJOIN**）：

```sql
INSERT OVERWRITE TABLE ads_xxx_user_order_di PARTITION (dt = '${bizdate}')
SELECT
    a.user_id,
    a.order_id,
    a.order_amt,
    b.shop_name,
    b.status                                 AS shop_status
FROM   dwd_xxx_order_di a
LEFT JOIN (
    SELECT shop_id, shop_name, status
    FROM   dim_xxx_shop_df
    WHERE  dt = '${bizdate}'
      AND  status = 1
) b
       ON a.shop_id = b.shop_id
WHERE  a.dt = '${bizdate}'
;
```

如确认维度表行数 < 几十万，可加 `/*+ MAPJOIN(b) */`：
```sql
SELECT /*+ MAPJOIN(b) */ ...
```

## 7. 上线前验数 SQL

### V1. 源分区行数
```sql
SELECT COUNT(1) AS row_cnt FROM dwd_xxx_order_di WHERE dt = '${bizdate}';
SELECT COUNT(1) AS row_cnt FROM dim_xxx_shop_df WHERE dt = '${bizdate}';
```

### V2. 目标分区行数
```sql
SELECT COUNT(1) AS row_cnt FROM ads_xxx_user_order_di WHERE dt = '${bizdate}';
```

### V3. 目标表主键唯一
```sql
-- 需要业务确认主键，假设为 (dt, order_id)
SELECT order_id, COUNT(1) AS cnt
FROM   ads_xxx_user_order_di
WHERE  dt = '${bizdate}'
GROUP BY order_id
HAVING COUNT(1) > 1
LIMIT 50;
```

### V4. 核心字段空值
```sql
SELECT SUM(CASE WHEN order_id  IS NULL THEN 1 ELSE 0 END) AS order_id_null_cnt,
       SUM(CASE WHEN order_amt IS NULL THEN 1 ELSE 0 END) AS order_amt_null_cnt,
       SUM(CASE WHEN shop_name IS NULL THEN 1 ELSE 0 END) AS shop_name_null_cnt,
       COUNT(1)                                          AS row_cnt
FROM   ads_xxx_user_order_di
WHERE  dt = '${bizdate}';
```

### V5. JOIN 前后行数对比
```sql
-- 5a. JOIN 前订单行数
SELECT COUNT(1) AS left_cnt FROM dwd_xxx_order_di WHERE dt = '${bizdate}';

-- 5b. JOIN 后行数（即目标表行数）
SELECT COUNT(1) AS joined_cnt FROM ads_xxx_user_order_di WHERE dt = '${bizdate}';

-- 期望：joined_cnt = left_cnt（修复 LEFT JOIN 退化后）
```

### V6. 昨天 vs 今天指标波动
```sql
SELECT dt, COUNT(1) AS row_cnt, SUM(order_amt) AS gmv
FROM   ads_xxx_user_order_di
WHERE  dt IN ('${bizdate}', '${yyyymmdd_1}')
GROUP BY dt
ORDER BY dt;
```

### V7. 近 7 天分区数据量
```sql
SELECT dt, COUNT(1) AS row_cnt
FROM   ads_xxx_user_order_di
WHERE  dt BETWEEN '${yyyymmdd_7}' AND '${bizdate}'
GROUP BY dt
ORDER BY dt;
```

### V8. COUNT DISTINCT 校验
- 本 SQL 没有 COUNT DISTINCT 指标，跳过 V8

### V9. 右表关联键唯一性
```sql
SELECT shop_id, COUNT(1) AS cnt
FROM   dim_xxx_shop_df
WHERE  dt = '${bizdate}'
GROUP BY shop_id
HAVING COUNT(1) > 1
LIMIT 50;
-- 期望：返回 0 行；若有重复，dim 表需先去重
```

## 8. 需要我补充的信息
- `dim_xxx_shop_df` 的实际大小（small / medium / large），决定是否启用 MAPJOIN
- `dim_xxx_shop_df` 的 `shop_id` 是否唯一（结合 `dt` 后是否唯一）
- 业务上 “shop_status = 1” 的定义：是否要保留无匹配店铺 / 已下线店铺的订单
- 目标表 `ads_xxx_user_order_di` 的主键（用于 V3 验数）
- LogView：总耗时、最慢 stage 类型、reducer / joiner 数量、是否长尾
- 是否调度任务（如调度，需要结合调度系统的日期变量定义）

> 本报告基于静态分析与业务上下文，未连接 ODPS。最终风险结论需要
> 结合数据量、LogView、业务口径、调度上下文复核。
