# odps-sql-review-skill — common developer tasks.
#
# Use `make help` to see what is available.

PYTHON ?= python3

.PHONY: help install dev test review review-all package clean

help:
	@echo "Available targets:"
	@echo "  install      - pip install -e . (editable install)"
	@echo "  dev          - install with dev extras (pytest, pyyaml, sqlglot)"
	@echo "  test         - run pytest"
	@echo "  review       - review skill_package/odps-sql-review/examples/bad_left_join.sql in markdown"
	@echo "  review-all   - review every example file in markdown"
	@echo "  package      - build dist/odps-sql-review-skill.zip"
	@echo "  clean        - remove caches and build artefacts"

install:
	$(PYTHON) -m pip install -e .

dev:
	$(PYTHON) -m pip install -e .[dev]

test:
	$(PYTHON) -m pytest -q

review:
	$(PYTHON) -m odps_sql_review skill_package/odps-sql-review/examples/bad_left_join.sql --format markdown

review-all:
	@for f in skill_package/odps-sql-review/examples/bad_*.sql; do \
		echo "===== $$f ====="; \
		$(PYTHON) -m odps_sql_review $$f --format markdown; \
		echo; \
	done

package:
	$(PYTHON) scripts/package_skill.py

clean:
	rm -rf dist build .pytest_cache __pycache__ */__pycache__ */*/__pycache__ \
		*.egg-info .ruff_cache .mypy_cache
