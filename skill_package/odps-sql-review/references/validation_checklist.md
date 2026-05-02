# 上线前验数 SQL 清单（validation_checklist）

> 本文用于指导 AI Agent 在评审报告的 **第 7 节 “上线前验数 SQL”**
> 中生成 “可粘贴到 ODPS Studio / DataWorks 直接跑” 的验数模板。
>
> 这些 SQL **不是由本 skill 执行**，而是给用户参考。模板里必须保留
> 占位符（`${bizdate}` / `<右表唯一键>` / `<目标表>`），强制用户
> 填入真实值，避免 “盲跑”。

---

## 一、必备的验数项

每份报告至少包含以下 9 类验数 SQL：

| 编号 | 名称 | 触发条件 | 目的 |
|---|---|---|---|
| V1 | 源分区行数检查 | 每张物理源表 | 确认源表当天有数据 |
| V2 | 目标分区行数检查 | 有 INSERT OVERWRITE 时 | 确认目标分区写入成功且行数合理 |
| V3 | 目标表主键重复检查 | 写入完成后 | 防止 JOIN 行数膨胀写入 |
| V4 | 核心字段空值检查 | 关键指标列 | 防止 NULL 污染 |
| V5 | JOIN 前后行数对比 | 出现 JOIN 时 | 验证是否发生行数膨胀 |
| V6 | 昨天 vs 今天指标波动 | 调度任务 | 防止指标突变 |
| V7 | 近 7 天分区数据量 | 调度任务 | 评估是否异常大 / 异常小 |
| V8 | COUNT DISTINCT 校验 | 出现 COUNT DISTINCT 时 | 与 approx_distinct / 中间表交叉验证 |
| V9 | 右表关联键唯一性 | 出现 JOIN 时 | 验证唯一键真实性 |

---

## 二、模板写法

### V1. 源分区行数检查
```sql
-- 1. 源表分区行数检查
SELECT COUNT(1) AS row_cnt
FROM   <源表>
WHERE  dt = '${bizdate}'   -- 替换为实际分区列
;
```

### V2. 目标分区行数检查
```sql
-- 2. 目标分区行数检查
SELECT COUNT(1) AS row_cnt
FROM   <目标表>
WHERE  dt = '${bizdate}'
;
```

### V3. 目标表主键重复检查
```sql
-- 3. 目标表主键重复检查
SELECT <主键字段>, COUNT(1) AS cnt
FROM   <目标表>
WHERE  dt = '${bizdate}'
GROUP BY <主键字段>
HAVING COUNT(1) > 1
LIMIT 50;
```

### V4. 核心字段空值检查
```sql
-- 4. 核心字段空值检查
SELECT SUM(CASE WHEN <核心字段1> IS NULL THEN 1 ELSE 0 END) AS f1_null_cnt,
       SUM(CASE WHEN <核心字段2> IS NULL THEN 1 ELSE 0 END) AS f2_null_cnt,
       COUNT(1)                                              AS row_cnt
FROM   <目标表>
WHERE  dt = '${bizdate}'
;
```

### V5. JOIN 前后行数对比
```sql
-- 5a. JOIN 前左表行数
SELECT COUNT(1) AS left_cnt
FROM   <左表>
WHERE  dt = '${bizdate}'
;

-- 5b. JOIN 后行数
SELECT COUNT(1) AS joined_cnt
FROM   (<在此粘贴你的 JOIN 子查询>) t
;
-- 评估：joined_cnt / left_cnt 应在业务预期范围内
```

### V6. 昨天 vs 今天指标波动
```sql
-- 6. 昨天 vs 今天指标波动
SELECT dt,
       <核心指标1>,
       <核心指标2>
FROM   <目标表>
WHERE  dt IN ('${bizdate}', '${yyyymmdd_1}')
ORDER BY dt
;
```

### V7. 近 7 天分区数据量
```sql
-- 7. 近 7 天分区数据量
SELECT dt, COUNT(1) AS row_cnt
FROM   <目标表>
WHERE  dt BETWEEN '${yyyymmdd_7}' AND '${bizdate}'
GROUP BY dt
ORDER BY dt
;
-- 评估：每日行数是否平稳，无突增 / 突降
```

### V8. COUNT DISTINCT 校验
```sql
-- 8a. 精确去重
SELECT COUNT(DISTINCT user_id) AS exact_uv
FROM   <表>
WHERE  dt = '${bizdate}'
;

-- 8b. 近似去重（用作交叉验证）
SELECT approx_distinct(user_id) AS approx_uv
FROM   <表>
WHERE  dt = '${bizdate}'
;
-- 评估：approx_uv 应在 exact_uv ± 2% 以内
```

### V9. 右表关联键唯一性
```sql
-- 9. 右表关联键重复检查
SELECT <关联键>, COUNT(1) AS cnt
FROM   <右表>
WHERE  dt = '${bizdate}'
GROUP BY <关联键>
HAVING COUNT(1) > 1
LIMIT 50;
-- 期望：返回 0 行
```

---

## 三、按场景增补

### 出现动态分区时
```sql
-- 写入完成后，统计目标表近 7 天每个动态分区的行数
SELECT dt, COUNT(1) AS row_cnt
FROM   <目标表>
WHERE  dt BETWEEN '${yyyymmdd_7}' AND '${bizdate}'
GROUP BY dt
ORDER BY dt;
-- 警惕：是否产生大量小分区 / 是否写入到错误的 dt
```

### 出现 LEFT JOIN 时
```sql
-- 验证 LEFT JOIN 是否被 WHERE 退化
-- a. 计算左表全量行数
SELECT COUNT(1) AS left_total FROM <左表> WHERE dt = '${bizdate}';

-- b. 计算 JOIN 后保留的左表行数（去重 left_key）
SELECT COUNT(DISTINCT <左表关联键>) AS left_kept
FROM   (<JOIN 结果>) t
;
-- 期望：left_kept = left_total（如果业务期望保留全部左表）
```

### 出现 BETWEEN 日期边界时
```sql
-- 单独统计边界两天，确认是否多算 / 少算
SELECT dt, <指标>
FROM   <表>
WHERE  dt IN ('<start_dt>', '<end_dt>')
GROUP BY dt
ORDER BY dt;
```

### 出现 INSERT OVERWRITE 静态分区时
```sql
-- 写入前确认目标分区当前数据
SELECT COUNT(1) FROM <目标表> WHERE dt = '${bizdate}';
-- 写入后再次统计，与上面对比
```

---

## 四、原则

1. 验数 SQL 必须包含 **占位符**，不允许直接写 “跑就完事” 的语句。
2. 不要在验数 SQL 里使用真实公司表名 / 真实生产分区。
3. 所有验数 SQL 都建议在 **目标表的最近 1-2 个分区** 上跑，避免
   全表扫描。
4. 每条验数 SQL 都要写明 **期望结果**（=0 行 / 在 ±X% 以内 / 平稳
   等）。
5. 报告里第 7 节的 SQL 必须按 V1-V9 的顺序输出，缺项要写明原因
   （例如 “本 SQL 无 INSERT OVERWRITE，跳过 V2”）。
