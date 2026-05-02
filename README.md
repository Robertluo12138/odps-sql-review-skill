# odps-sql-review-skill

Portable, CLI-agnostic Agent Skill that reviews ODPS / MaxCompute SQL
for production risks (correctness) and performance issues (skew, COUNT
DISTINCT, MAU/YAU, MAPJOIN, dynamic partitions, …).

## What this is

`odps-sql-review-skill` is a **file-based skill package**.  It is a
plain folder containing:

- `SKILL.md` at the root with the skill manifest;
- `references/` with detailed rule documents;
- `examples/` with bad SQL samples to demonstrate findings;
- `odps_sql_review/` Python package with the static-analysis engine;
- `scripts/` thin wrappers for users / agents that look for `scripts/`;
- `bin/odps-sql-review` so you can run the CLI directly from a checkout.

It is intentionally **not tied to Claude Code** or any other AI CLI:

- It does **not** install into `~/.claude/skills`.
- It does **not** require `CLAUDE_SKILL_DIR` or any environment variable.
- It does **not** require you to be inside any specific AI agent.
- The repository folder itself is the skill - any AI CLI that can read
  a `SKILL.md` file from disk can use it.

## Two ways to use it

### Mode A - Agent Skill mode

Any AI CLI / agent that supports file-based skills can read
`SKILL.md` and follow its instructions.  Two common patterns:

1. **Drop the folder into your agent's skill directory.** Most agents
   pick up file-based skills by name.
2. **Tell the agent to read the file.** Examples (paraphrased):
   - "Read `SKILL.md` in this folder and review my SQL using this skill."
   - "Use the rules in `references/review_rules.md` and
     `references/odps_performance_rules.md` to review the SQL I am
     about to paste."
   - "Run `python -m odps_sql_review query.sql` and convert the output
     into a final review."

### Mode B - Direct Python CLI mode

You do not need any AI CLI.  The Python CLI works on its own:

```bash
python -m odps_sql_review path/to/query.sql
./bin/odps-sql-review path/to/query.sql
```

CLI flags:

```
positional arguments:
  sql_path                Path to a SQL file. Use '-' for STDIN.

options:
  --format {markdown,json}      Output format (default: markdown).
  --output PATH                 Write the report to a file.
  --table-profile PATH          YAML file describing tables.
  --generate-validation-sql     Print only the validation SQL templates.
  --focus {correctness,performance,all}
                                Filter findings to a single area.
  --verbose                     Extra diagnostic output.
  --version                     Show version and exit.
```

## Installation

### Option A - install as a Python package

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
odps-sql-review examples/bad_left_join.sql
```

### Option B - run as a module

```bash
python -m odps_sql_review examples/bad_left_join.sql
```

### Option C - run the launcher directly

```bash
./bin/odps-sql-review examples/bad_left_join.sql
```

(All three commands run the same code.  Pick whichever is most
convenient.)

### Optional dependencies

- [`pyyaml`](https://pypi.org/project/pyyaml/) - enables
  `--table-profile path/to/table_profile.yml` for richer reviews.
- [`sqlglot`](https://pypi.org/project/sqlglot/) - enables a structural
  parse fallback.  The tool already works without it via regex.

```bash
pip install pyyaml sqlglot
```

## Use it with any AI CLI

Three patterns work today, regardless of which agent you use:

1. **Read the skill manifest**:
   > "Read `SKILL.md` in this folder and review my SQL using this skill.
   > Output the standard 风险审查报告 (risk review report)."
2. **Paste the SQL inline**:
   > "Follow the rules in `SKILL.md` and tell me whether this SQL is
   > safe to release.  Use the report template in
   > `references/output_template.md`."
3. **Ask the agent to call the CLI**:
   > "Run `python -m odps_sql_review query.sql --format markdown`,
   > then summarise the findings and add any business risk you spot."

## Package the skill for distribution

```bash
python scripts/package_skill.py
# -> dist/odps-sql-review-skill.zip
```

The resulting zip:

- contains `SKILL.md` directly at the root (no leading folder);
- excludes caches (`__pycache__`, `.pytest_cache`, `.git`, `dist`, …);
- can be uploaded to GitHub Releases or shared as a single artefact;
- can be unzipped into any directory and reused without modification.

## Upload to GitHub

```bash
git init
git add .
git commit -m "init odps-sql-review-skill"
git branch -M main
git remote add origin git@github.com:<your-org>/odps-sql-review-skill.git
git push -u origin main
```

Optional: cut a release with the prebuilt zip:

```bash
python scripts/package_skill.py
gh release create v0.1.0 dist/odps-sql-review-skill.zip --notes "Initial release"
```

## Maintain `table_profile.yml`

Static analysis cannot guess whether a table is a fact table or a
dimension table, what its grain is, or whether a column is a unique
key.  Provide a `table_profile.yml` (template:
`references/table_profile_template.yml`) and pass it via
`--table-profile` so the review can use real metadata.

```yaml
tables:
  dwd_xxx_user_active_di:
    partition_cols: ["dt"]
    grain: ["dt", "user_id"]
    unique_keys:
      - ["dt", "user_id"]
    size_level: "large"
    table_type: "fact"
    description: "Daily user active table"
```

## Provide LogView for deeper performance diagnosis

When the SQL has already run at least once, paste the fields listed in
`references/logview_template.md` along with your SQL.  The agent then
has enough information to give specific reducer/joiner / parallelism
advice instead of generic guidance.

## Limitations

- Static analysis cannot know the *actual* size of a table.  Without
  `table_profile.yml`, the tool errs on the side of "this might be a
  big partitioned table".
- Static analysis cannot prove a column is a unique key.  When the
  `table_profile.yml` does not declare `unique_keys`, the report marks
  the JOIN as "需要确认".
- Static analysis cannot verify business semantics.  Metric definitions
  always need business confirmation.
- The tool **never connects to ODPS** and **never executes SQL**.
- Performance SET parameters are intentionally *not* hard-coded.  They
  require LogView and resource quota information to be useful.

## Layout

```
odps-sql-review-skill/
├── SKILL.md
├── README.md
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── Makefile
├── bin/
│   └── odps-sql-review
├── odps_sql_review/        # Python package
├── scripts/                # thin compatibility wrappers
├── references/             # rule documents and templates
├── examples/               # sample SQL + expected report
└── tests/                  # unit tests
```

## License

MIT - see `LICENSE`.
