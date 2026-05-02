# LogView 摘要模板

> 当 SQL 已经在 ODPS 上跑过一次（或者你能拿到一次完整的 LogView）
> 时，请把以下信息粘贴给 AI Agent，它就能给出更精准的性能诊断。
> 没有 LogView 时，性能建议会按 “需要结合 LogView / 数据量确认”
> 给出方向，不会输出具体的 reducer / joiner 数值。

## 一、必填字段

```
任务总耗时: <例如 45 分钟>
最慢 stage ID: <例如 M3>
stage 类型: <Join / Group By / Count Distinct / Row Number / Dynamic Partition>
输入行数: <例如 12_345_678_900>
输入字节数: <例如 4.2 TB>
reducer / joiner 数: <例如 1024>
单 worker 最长耗时: <例如 12 分钟>
单 worker 平均耗时: <例如 1 分钟>
单 worker 最大输入行数: <例如 5 亿行>
单 worker 平均输入行数: <例如 200 万行>
是否只有少数 worker 慢: 是 / 否
是否所有 worker 都慢: 是 / 否
```

## 二、诊断对照

- 少数 worker 慢 → **倾斜**（数据热点，单点压力大）。
- 所有 worker 都慢 → **数据量过大或并行度不足**。
- JOIN stage 慢 → 检查 JOIN key 是否倾斜、右表是否需要去重、
  是否可以 MAPJOIN / DISTMAPJOIN。
- GROUP BY stage 慢 → 检查 GROUP BY key 分布、是否需要二阶段聚合或
  `SET odps.sql.groupby.skewindata=true`。
- COUNT DISTINCT stage 慢 → 高基数字段需要预去重，长周期建议中间表。
- ROW_NUMBER stage 慢 → PARTITION BY key 倾斜。
- 动态分区 stage 慢 → 分区数量不可控，可能产生大量小文件，需要
  `odps.sql.reshuffle.dynamicpt` 配合数据量评估。

## 三、可选字段

- 项目资源配额（CU / quota slot）；
- 写入字节数；
- spill 量 / temp 表大小；
- 同一任务历史平均耗时；
- 周期任务在过去 7 天的耗时趋势。

> 提示：以上信息越完整，性能建议越具体。
