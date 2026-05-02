# odps-sql-review-skill

A **portable Agent Skill** for reviewing ODPS / MaxCompute SQL before
release. The main artefact is a zip you import into your AI tool
(QoderWork, etc.) — **no Python required for normal use**.

The repository also ships an *optional* Python static checker for
people who want to run a deterministic pre-flight locally; it lives
under `odps_sql_review/` and is documented at the bottom of this file.

---

## What this is

- A skill folder (`skill_package/odps-sql-review/`) containing a
  comprehensive `SKILL.md`, rule documents, examples and templates.
- A packaging script (`scripts/package_skill.py`) that produces three
  zip artefacts ready for distribution.
- An optional Python CLI (`odps_sql_review/`) that performs the same
  static review locally without any AI agent.

The whole repository can be uploaded to GitHub. Only the contents of
`skill_package/odps-sql-review/` are needed for QoderWork import.

## Package the skill

```bash
python scripts/package_skill.py
```

This creates three zips under `dist/`:

| Zip | When to use |
|---|---|
| `dist/odps-sql-review-qoderwork-flat.zip`   | **Try this first** in QoderWork. `SKILL.md` sits at the zip root. |
| `dist/odps-sql-review-qoderwork-folder.zip` | Fallback. Same content but inside an `odps-sql-review/` folder. |
| `dist/odps-sql-review-full-repo.zip`        | Full repo (skill + optional Python CLI + docs + scripts). |

You can also build a single artefact:

```bash
python scripts/package_skill.py --only flat
python scripts/package_skill.py --only folder
python scripts/package_skill.py --only full
```

## Import into QoderWork

1. Run `python scripts/package_skill.py`.
2. Import `dist/odps-sql-review-qoderwork-flat.zip` into QoderWork.
3. If that zip is rejected, retry with
   `dist/odps-sql-review-qoderwork-folder.zip`.

After import the skill is named `odps-sql-review` and is invokable by
any prompt that references it.

## Use the skill

Paste a SQL into QoderWork (or any other AI tool that loaded the
skill) and ask:

> Use the `odps-sql-review` skill to review this SQL.

The agent will read `SKILL.md`, follow the workflow, and output the
8-section Chinese review report defined in
`references/output_template.md`.

For a richer prompt with `table_profile` and LogView slots, see
`skill_package/odps-sql-review/templates/review_prompt.md`.

## What NOT to upload

When you paste SQL or context into a third-party AI tool, **do not**
include:

- real company SQL with internal table names, library names, project
  names, or columns that contain personal data — replace them with
  fake names like `fake_dwd_user_action_di`;
- real `table_profile.yml` from production;
- real LogView links or full LogView dumps — only paste the summary
  fields listed in
  `skill_package/odps-sql-review/templates/logview_template.md`;
- AK / SK / token / endpoint / internal links;
- any user-level credential.

If you cannot redact the data, run the **optional Python static
checker** locally inside your intranet (see the next section).

## Repository layout

```
odps-sql-review-skill/
├── README.md                          <- you are here
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── Makefile
├── .gitignore
│
├── skill_package/
│   └── odps-sql-review/                <- the pure skill package
│       ├── SKILL.md
│       ├── references/
│       │   ├── review_rules.md
│       │   ├── performance_rules.md
│       │   ├── metric_rules.md
│       │   ├── validation_checklist.md
│       │   └── output_template.md
│       ├── examples/
│       │   ├── bad_left_join.sql
│       │   ├── bad_count_distinct.sql
│       │   ├── bad_no_partition.sql
│       │   ├── bad_join_explosion.sql
│       │   ├── bad_dynamic_partition.sql
│       │   └── expected_report.md
│       └── templates/
│           ├── table_profile_template.yml
│           ├── logview_template.md
│           └── review_prompt.md
│
├── scripts/
│   └── package_skill.py                <- builds the three dist zips
│
├── odps_sql_review/                    <- OPTIONAL Python CLI (see below)
├── bin/odps-sql-review                 <- CLI launcher (optional)
└── tests/                              <- unit tests for the Python CLI
```

`skill_package/odps-sql-review/` is the **only** thing QoderWork needs.

---

## Optional: Python static checker

> The Python CLI is **optional**. The skill works fine without it.
> Use the CLI if you want to run a deterministic static analysis
> locally (e.g. inside your company intranet, or as a CI gate).

### Quick run from a checkout

```bash
python -m odps_sql_review skill_package/odps-sql-review/examples/bad_left_join.sql
./bin/odps-sql-review skill_package/odps-sql-review/examples/bad_left_join.sql
```

### Install as a package

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
odps-sql-review skill_package/odps-sql-review/examples/bad_left_join.sql
```

### CLI flags

```
positional:
  sql_path                       Path to a SQL file. Use '-' for STDIN.

options:
  --format {markdown,json}       Output format (default: markdown).
  --output PATH                  Write report to a file instead of stdout.
  --table-profile PATH           YAML file describing tables.
  --generate-validation-sql      Print only the validation SQL templates.
  --focus {correctness,performance,all}
  --verbose                      Extra diagnostic output.
  --version                      Show version and exit.
```

### Optional Python dependencies

- `pyyaml` — enables `--table-profile`. Install with `pip install pyyaml`.
- `sqlglot` — structural parse fallback. Install with `pip install sqlglot`.

The CLI also works with neither installed (regex fallback).

### Run the test suite

```bash
python -m pytest -q
```

### CI usage

`odps-sql-review` exits with status `1` when it detects a blocking
high-risk issue, so it is suitable as a PR gate:

```bash
odps-sql-review build/release.sql --format markdown --output review.md
```

Exit codes: `0` = no blocker · `1` = blocker found · `2` = arg / IO error.

---

## License

MIT — see `LICENSE`.
