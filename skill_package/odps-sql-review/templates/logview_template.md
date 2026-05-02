# LogView 信息粘贴模板

> 本模板用于让 AI Agent 拿到 “够用的” LogView 摘要，从而给出更
> 精准的性能建议。**没有 LogView 时**，性能建议只能给方向，
> 不会给出 reducer / joiner 的具体数字。

---

## 一、必填项（最少粘贴这些）

```
任务总耗时：           例如 45 分钟
最慢 stage ID：        例如 M3
stage 类型：           Join / Group By / Count Distinct / Row Number / Dynamic Partition / Map / Reduce
输入行数：             例如 12_345_678_900
输入字节数：           例如 4.2 TB
reducer / joiner 数：  例如 1024
单 worker 最长耗时：   例如 12 分钟
单 worker 平均耗时：   例如 1 分钟
单 worker 最大输入行数：例如 5 亿
单 worker 平均输入行数：例如 200 万
是否只有少数 worker 慢：是 / 否
是否所有 worker 都慢： 是 / 否
```

---

## 二、可选项（越完整建议越具体）

```
项目资源配额（CU / quota slot）：
任务历史平均耗时：
近 7 天耗时趋势：     稳定 / 上涨 / 下降
spill 量 / temp 表大小：
写入字节数：
是否有重试 / OOM：
LogView 链接（不要发出去，只用于自查）：
```

> 警告：不要把内部 LogView 链接、AK / SK、内网 endpoint 复制到
> 第三方 AI 工具里。本 skill 只需要上面的指标摘要，不需要原始链接。

---

## 三、AI Agent 如何用这些信息

| 现象 | 大概率原因 | 建议方向 |
|---|---|---|
| 少数 worker 慢，其余快 | 数据倾斜 | 倾斜诊断（GROUP BY / JOIN / ROW_NUMBER 的 key 分布） |
| 所有 worker 都慢 | 数据量过大 / 并行度不足 | 评估 reducer / joiner 数；分区裁剪是否生效 |
| JOIN stage 慢 | JOIN key 倾斜 / 行数膨胀 / 大表 JOIN 大表 | 检查 JOIN key、是否 MAPJOIN、是否 SKEW JOIN |
| GROUP BY stage 慢 | GROUP BY key 倾斜 | 加 `odps.sql.groupby.skewindata = true`、二阶段聚合 |
| COUNT DISTINCT stage 慢 | 高基数字段 + 长周期 | 中间表 + 二次聚合；接受近似可用 `approx_distinct` |
| ROW_NUMBER stage 慢 | PARTITION BY key 倾斜 | 改两阶段方法、热点 key 单独处理 |
| 动态分区 stage 慢 | 分区数量不可控 / 大量小文件 | `odps.sql.reshuffle.dynamicpt` 评估；按分区拆分 |

---

## 四、Agent 输出要求

- 只要用户提供了 LogView 摘要，第 4 节 “Reducer / Joiner / 并行度
  建议” 必须给出具体方向，不能再用 “需要结合 LogView 确认” 搪塞。
- 没有 LogView 时，第 4 节必须明确写 “需要结合 LogView / 数据量确认”，
  并把上面的 “必填项” 列表贴回给用户。
