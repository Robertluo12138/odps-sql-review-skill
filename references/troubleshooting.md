# 常见问题排查

## 1. 报告里把维度小表标成 “缺少分区过滤”
原因：静态分析无法判断一张表是不是真的小维度表。
解决：在 `table_profile.yml` 中显式声明：

```yaml
tables:
  dim_xxx_shop_df:
    partition_cols: []
    size_level: "small"
    table_type: "dimension"
```

加载后再跑：

```bash
odps-sql-review query.sql --table-profile path/to/table_profile.yml
```

## 2. 误报了 LEFT JOIN 退化
原因：当 WHERE 中确实是 `b.col IS NULL` 这样的 anti-join 用法时，
工具默认会跳过；但对于复杂表达式可能仍会误报。
解决：在 SQL 中把过滤条件移到 ON 子句里，或在评审报告里手动注明
“此处为 anti join，需保留 b 字段过滤”。

## 3. CLI 在 Windows 上无法运行 `./bin/odps-sql-review`
原因：`bin/odps-sql-review` 是一个无扩展名的 Python 脚本，Windows
默认按可执行文件查找。
解决：

```bat
python -m odps_sql_review path\to\query.sql
```

或安装后使用 `odps-sql-review.exe` 入口（pyproject.toml 里已声明
console_script）。

## 4. PyYAML 未安装时如何继续使用 table profile？
工具会打印一条 warning 并跳过 profile 加载。如需启用：

```bash
pip install pyyaml
```

## 5. AI CLI 找不到 SKILL.md 怎么办？
- 确认仓库根目录直接包含 `SKILL.md`；
- 若使用 zip 分发，请使用 `python scripts/package_skill.py` 生成
  `dist/odps-sql-review-skill.zip`，解压后 `SKILL.md` 应该在压缩包根目录；
- 不同 AI CLI 对 skill 目录的命名约定不同，可能需要把 `odps-sql-review-skill`
  目录放到对应 CLI 的 skill 搜索路径下；本仓库本身不依赖任何特定的
  AI CLI 路径。

## 6. 如何在 CI 中使用？
`odps-sql-review` 在检测到 blocking 问题时会以 exit code 1 退出，
适合 PR 拦截：

```bash
odps-sql-review build/release.sql --format markdown --output review.md
```

退出码：
- 0：未发现阻塞问题；
- 1：检测到阻塞问题（高风险且 blocking=true）；
- 2：参数错误或 IO 错误。
