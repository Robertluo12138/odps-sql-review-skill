"""Allow ``python -m odps_sql_review`` to invoke the CLI."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
