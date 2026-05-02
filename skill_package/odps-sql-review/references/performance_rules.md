# 性能优化规则手册（performance_rules）

> 本文按主题列出常见性能优化方向。AI Agent 在评审时应当 **引用本文**
> 给出建议，而不是凭记忆给出固定的 SET 数值。
>
> 任何 SET 参数都必须强调 “需要结合 LogView / 数据量确认”。
> 没有 LogView 时，给方向，不给具体数字。

---

## 一、分区裁剪（Partition Pruning）

- 每张物理源表都必须有分区过滤；
- 优先在 **最内层子查询** 中过滤分区，避免依赖最外层 WHERE；
- 不要在分区列上使用函数（`substr` / `cast` / `date_format` /
  `from_unixtime` / `to_date`），否则 partition pruning 会失效；
- 长周期场景（30 天 / 365 天）必须警惕：
  - 是否可以使用按天聚合的中间表 `dws_xxx_user_active_di`；
  - 是否可以使用日 UV 中间表 + 滚动表方式；
  - 是否可以用 `approx_distinct` 做监控指标；
- 分区 IN 列表 / BETWEEN 区间均可裁剪，但函数包裹后会失效。

---

## 二、Join 优化

### 1. 大表 JOIN 小表 → 候选 MAPJOIN
- 写法：在 `SELECT` 后加 `/*+ MAPJOIN(small_alias) */`；
- 仅 hint 小表别名，不要 hint 大表；
- 对 `LEFT JOIN`，左侧通常是要保留的大表；
- “是否真小表” 必须确认（一般 < 512 MB 才适合 MAPJOIN）；
- 维度表 `dim_xxx_df` 通常是 MAPJOIN 候选，但仍需确认实际大小。

### 2. 中表 JOIN 大表 → 候选 DISTMAPJOIN
- 适用条件：右表中等大小，单 worker 内存可承受；
- 是否启用必须结合 LogView。

### 3. 热点 key → 候选 SKEW JOIN
- 写法：
  ```sql
  SET odps.sql.skewjoin = true;
  SET odps.sql.skewjoin.key = '<热点key>';
  ```
- **不要** 同时盲目使用 MAPJOIN 与 SKEW JOIN；
- 已知热点 key 时可手动拆分热点处理后 `UNION ALL` 非热点。

### 4. 仅判断存在性
- 使用 `SEMI JOIN` / `LEFT SEMI JOIN` / `EXISTS` 替代
  `JOIN + DISTINCT`；
- `LEFT ANTI JOIN` / `NOT EXISTS` 替代 `LEFT JOIN ... WHERE b.x IS NULL`。

### 5. JOIN 前必做
- 子查询里只 SELECT 必要字段；
- 提前过滤分区与无关数据；
- 类型不一致时显式 CAST 并确认是否被静默过滤；
- JOIN 多张事实表时，必须确认右表唯一键。

---

## 三、GROUP BY / COUNT DISTINCT 优化

- COUNT DISTINCT 是性能重灾区，单 reducer 全局去重；
- 业务允许时，使用 `approx_distinct` 并在文档中标注 “近似值”；
- 严格精确去重时：
  1. 先按分区与列过滤；
  2. 在最小粒度（如 `user_id`）去重；
  3. 再在更大窗口聚合；
  4. **禁止把日 UV 简单相加得到 MAU / YAU**；
- 怀疑 GROUP BY 倾斜时：
  - 加 `SET odps.sql.groupby.skewindata = true;` （确认存在倾斜后再开）；
  - 已知热点 key 用二阶段聚合（加盐预聚合 + 二次聚合）：
    ```sql
    -- 第一阶段：加盐
    SELECT key, salt, sum(amt) AS sub_amt
    FROM   t
    GROUP BY key, salt;
    -- 第二阶段：去盐
    SELECT key, sum(sub_amt)
    FROM   ...
    GROUP BY key;
    ```
- `GROUPING SETS` / `CUBE` / `ROLLUP` 可以减少多次扫描。

---

## 四、MAU / YAU / 长周期去重指标（重点）

这是最常见的 ODPS 性能瓶颈，也是这套规则强调最多的场景。

### 不要
- **不要** 直接对 30 / 365 天分区做 `COUNT(DISTINCT user_id)`；
- **不要** 把日 UV 简单相加得到 MAU / YAU（会重复计算同一用户）；
- **不要** 在原始明细表（`dwd_*_di`）上跑长周期去重。

### 推荐做法

1. 落地用户日活中间表：
   ```
   表名：dws_xxx_user_active_di
   分区：dt
   粒度：dt + user_id
   ```
2. 在该中间表上做长周期去重：
   ```sql
   SELECT COUNT(DISTINCT user_id) AS mau
   FROM   dws_xxx_user_active_di
   WHERE  dt BETWEEN '${month_start}' AND '${bizdate}';
   ```
3. 仅监控趋势可使用 `approx_distinct`：
   ```sql
   SELECT approx_distinct(user_id) AS mau_approx
   FROM   dws_xxx_user_active_di
   WHERE  dt BETWEEN '${month_start}' AND '${bizdate}';
   ```
4. 365 天滚动方案：维护滚动用户全集表，注意精确状态维护成本
   （新增用户 / 流失用户的窗口滑动需要额外逻辑）。

### 评审时的话术
> 当前 SQL 在原始明细表上做 365 天 `COUNT DISTINCT`，几乎必然超时。
> 建议落地按天聚合的用户日活中间表 `dws_xxx_user_active_di`
> （dt + user_id 粒度），在该中间表上做月活 / 年活计算。
> 若业务接受近似值可使用 `approx_distinct`，需在文档中明确说明
> 不适用于财务等强精确场景。

---

## 五、Reducer / Joiner / 并行度

- 大 shuffle 场景：`GROUP BY` / `JOIN` / `DISTINCT` / `ORDER BY` /
  `WINDOW`；
- 可调整的参数（**不要给固定数字**）：
  - `odps.stage.reducer.num`
  - `odps.stage.joiner.num`
  - `odps.stage.mapper.split.size`
- 必须结合 LogView：
  - 输入数据量；
  - stage 最大耗时 / 平均耗时；
  - reducer / joiner 数；
  - 是否长尾 / 倾斜；
  - 项目资源配额（CU / quota slot）；
- 诊断对照：
  - **少数 reducer 慢** → 数据倾斜，先做倾斜诊断；
  - **全部 reducer 都慢** → 数据量过大或并行度不足；
  - **JOIN stage 慢** → 检查 JOIN key 倾斜 / 行数膨胀 /
    可否 MAPJOIN；
  - **GROUP BY stage 慢** → 检查 GROUP BY key 分布；
  - **COUNT DISTINCT stage 慢** → 高基数字段需要预去重；
  - **ROW_NUMBER stage 慢** → PARTITION BY key 倾斜。
- 没有 LogView 时，必须写 “需要结合 LogView / 数据量确认”。

---

## 六、动态分区

- 检查 `INSERT OVERWRITE ... PARTITION (dt)`（不带值）的动态分区
  数量上限；
- 数量小（如 < 100）：评估
  ```sql
  SET odps.sql.reshuffle.dynamicpt = false;
  ```
  减少 reducer，但要警惕小文件风险；
- 数量大（如 > 1000）：建议先按分区维度 GROUP BY，估算分布；
  部分分区远大于其他时，单独处理大分区 → `UNION ALL`；
- 写入前必须预估分区数量是否在合理范围；
- 任务上线前增加目标分区行数与主键校验（验数 SQL）。

---

## 七、ROW_NUMBER / TopN

- `ROW_NUMBER() OVER (PARTITION BY key ORDER BY ...)` 当 key 倾斜时
  会出现长尾任务；
- 仅需 TopN 时考虑：
  - 预聚合 + 排序；
  - 热点 key 单独处理；
  - 两阶段方法（先取每分区 TopN，再合并）；
- 必须确认 PARTITION BY key 的分布；
- TopN 可考虑 `qualify ROW_NUMBER() OVER (...) <= N`（语义更清晰）。

---

## 八、其它建议

- `ORDER BY` 必须配合 `LIMIT`，否则单 reducer 全局排序会很慢；
- `SELECT *` 在列存表上代价高；
- 重复使用的子查询建议落地为中间表，避免多次扫描；
- `UNION` 与 `UNION ALL` 的语义差异要明确，`UNION` 会去重，代价更高；
- 多次扫描相同分区时，考虑使用 `WITH ... AS (...)` 把通用子集提取
  出来，避免重复 IO。

---

## 九、何时建议建中间表 / 滚动表

满足以下任一条件时，建议向用户提议中间表 / 滚动表：

1. 同一聚合在多个报表中被复用 ≥ 3 次；
2. 长周期去重指标（MAU / YAU / 30d / 365d）；
3. JOIN 多张大事实表，且每天都要重算；
4. ROW_NUMBER 在大表上，每次都要重算 TopN；
5. 历史快照需求（每天保留全量用户状态）。

中间表设计要点：
- 明确粒度（dt + 唯一键）；
- 分区策略（按天 / 按小时）；
- 历史回刷成本；
- 谁来维护、SLA 多少。
