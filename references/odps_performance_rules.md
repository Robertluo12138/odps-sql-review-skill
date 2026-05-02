# ODPS / MaxCompute 性能优化规则手册

> 这份手册按主题列出常见的性能优化方向。AI Agent 在评审时应当
> 引用本文给出建议，而不是凭记忆给出固定的 SET 数值。
>
> 任何 SET 参数都必须强调 “需要结合 LogView / 数据量确认”。

## 一、分区裁剪

- 每张物理源表都必须有分区过滤；
- 优先在最内层子查询中过滤分区，避免依赖最外层 WHERE；
- 不要在分区列上使用函数（substr / cast / date_format），否则
  partition pruning 会失效；
- 30 天 / 365 天等长周期扫描需要明确：
  - 是否可以使用按天聚合的中间表 `dws_xxx_user_active_di`；
  - 是否可以使用日 UV 中间表 + 滚动表方式；
  - 是否可以用 approx_distinct 做监控指标。

## 二、Join 优化

### 1. 大表 JOIN 小表
- 候选：`/*+ MAPJOIN(small_alias) */`；
- 仅 hint 小表别名；
- 对 LEFT JOIN，左侧通常是要保留的大表；
- 是否真小表必须确认（通常 < 512 MB 才适合 MAPJOIN）。

### 2. 中表 JOIN 大表
- 候选：DISTMAPJOIN（仅在适用时）；
- 适用条件：右表中等大小，单 worker 内存可承受。

### 3. 热点 key
- 候选：`SET odps.sql.skewjoin=true;` 配合 `skewjoin_keys` 提示；
- 不要同时盲目使用 MAPJOIN 与 SKEW JOIN；
- 已知热点 key 时，可手动拆分热点处理后 UNION ALL。

### 4. 仅判断存在性
- 用 `SEMI JOIN` / `EXISTS` 替代 JOIN + DISTINCT。

### 5. JOIN 前必做
- 子查询里只 SELECT 必要字段；
- 提前过滤分区与无关数据；
- 类型不一致时显式 CAST，并注意是否会被静默过滤。

## 三、GROUP BY / COUNT DISTINCT 优化

- COUNT DISTINCT 是性能重灾区；
- 业务允许时，使用 `approx_distinct` 并在文档中标注 “近似”；
- 严格精确时：
  1. 先按分区与列过滤；
  2. 在最小粒度（如 user_id）去重；
  3. 再在更大窗口聚合；
  4. **禁止将日 UV 简单相加得到 MAU/YAU**；
- 怀疑 GROUP BY 倾斜时：
  - 加 `SET odps.sql.groupby.skewindata=true;`（确认存在倾斜后再开）；
  - 已知热点 key 用二阶段聚合（加盐预聚合 + 二次聚合）；
- 365 天窗口建议用日活中间表或滚动表。

## 四、MAU / YAU / 长周期去重指标

- 不要直接对 30/365 天分区做 `COUNT(DISTINCT user_id)`；
- 中间表设计：
  ```
  dws_xxx_user_active_di
  分区: dt
  粒度: dt + user_id
  ```
- MAU/YAU 由该表计算：
  ```sql
  SELECT COUNT(DISTINCT user_id)
  FROM   dws_xxx_user_active_di
  WHERE  dt BETWEEN '${month_start}' AND '${bizdate}'
  ```
- 仅监控趋势可使用 `approx_distinct`；
- 365 天滚动方案：维护一个滚动用户全集表，注意精确状态维护成本。

## 五、Reducer / Joiner / 并行度

- 大 shuffle 场景：GROUP BY、JOIN、DISTINCT、ORDER BY、WINDOW；
- 可调整：
  - `odps.stage.reducer.num`
  - `odps.stage.joiner.num`
  - `odps.stage.mapper.split.size`
- **禁止给出固定数字**，必须结合 LogView：
  - 输入数据量；
  - stage 最大耗时 / 平均耗时；
  - reducer / joiner 数；
  - 是否长尾 / 倾斜；
  - 项目资源配额。
- 仅个别 reducer 慢 → 倾斜诊断优先；
- 全部 reducer 都慢 → 数据量过大或并行度不足。

## 六、动态分区

- 检查动态分区数量上限；
- 数量小：评估 `SET odps.sql.reshuffle.dynamicpt=false;`，
  但要警惕小文件风险；
- 部分动态分区远大于其他时，单独处理大分区；
- 写入前预估分区数量是否在合理范围。

## 七、ROW_NUMBER / TopN

- ROW_NUMBER OVER (PARTITION BY key ORDER BY ...) 当 key 倾斜时
  会出现长尾任务；
- 仅需 TopN 时考虑：
  - 预聚合 + 排序；
  - 热点 key 单独处理；
  - 两阶段方法（先取每分区 TopN，再合并）；
- 必须确认 PARTITION BY key 的分布。

## 八、其它建议

- ORDER BY 必须配合 LIMIT，否则单 reducer 全局排序会很慢；
- SELECT 列尽量精简；
- 重复使用的子查询建议落地为中间表，避免多次扫描；
- UNION 与 UNION ALL 的语义差异要明确，UNION 会去重，代价更高。
