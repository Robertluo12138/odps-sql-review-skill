#!/usr/bin/env python3
"""scripts/package_skill.py

Build a distributable zip of the skill that places ``SKILL.md`` at the
root of the archive.  This makes the zip directly droppable into any AI
CLI / skill manager that consumes file-based skills, while still being
useful as a plain Python project.

Usage:
    python scripts/package_skill.py
    # writes dist/odps-sql-review-skill.zip

The zip excludes caches, virtual environments, and any previous build
artefacts so the archive stays small and reproducible.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from typing import Iterable, List, Set


_DEFAULT_EXCLUDE_DIRS: Set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".git",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".ruff_cache",
}

_DEFAULT_EXCLUDE_FILES: Set[str] = {
    ".DS_Store",
    "Thumbs.db",
}


def _iter_files(root: str, extra_excludes: Iterable[str] = ()) -> List[str]:
    """Walk ``root`` and yield every file path that should be packaged."""

    excluded_dirs = _DEFAULT_EXCLUDE_DIRS | set(extra_excludes)
    selected: List[str] = []
    for current, dirs, files in os.walk(root):
        # Mutate ``dirs`` in place so os.walk skips excluded directories.
        dirs[:] = [d for d in dirs if d not in excluded_dirs and not d.endswith(".egg-info")]
        for name in files:
            if name in _DEFAULT_EXCLUDE_FILES:
                continue
            if name.endswith((".pyc", ".pyo")):
                continue
            full_path = os.path.join(current, name)
            selected.append(full_path)
    return sorted(selected)


def build_zip(repo_root: str, output_path: str) -> None:
    """Write a zip archive of ``repo_root`` to ``output_path``.

    The archive is structured so that ``SKILL.md`` lives at the root of
    the archive (no leading folder).  AI agents and skill managers can
    read it without unwrapping a parent directory first.
    """

    files = _iter_files(repo_root, extra_excludes={os.path.basename(os.path.dirname(output_path))})
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # Remove any stale archive so we get a clean build.
    if os.path.exists(output_path):
        os.remove(output_path)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arcname = os.path.relpath(path, repo_root)
            # Skip the dist/ output itself if a previous archive remains.
            if arcname.startswith("dist" + os.sep) or arcname == "dist":
                continue
            zf.write(path, arcname)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the odps-sql-review skill into a zip.")
    parser.add_argument(
        "--repo-root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Root of the skill repository (default: parent of this script).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output zip path (default: <repo_root>/dist/odps-sql-review-skill.zip).",
    )
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    output = args.output or os.path.join(repo_root, "dist", "odps-sql-review-skill.zip")

    skill_md = os.path.join(repo_root, "SKILL.md")
    if not os.path.exists(skill_md):
        print(
            f"[package_skill] error: SKILL.md not found at {skill_md}. "
            "Aborting so the zip is never published without the skill manifest.",
            file=sys.stderr,
        )
        return 2

    build_zip(repo_root, output)
    size_kb = os.path.getsize(output) / 1024
    print(f"[package_skill] wrote {output} ({size_kb:.1f} KB)")

    # Best-effort summary of what was included so the user can sanity check.
    with zipfile.ZipFile(output) as zf:
        roots = sorted({name.split("/", 1)[0] for name in zf.namelist()})
        print(f"[package_skill] archive root entries: {', '.join(roots)}")
    return 0


# Allow import as module too.
def package_skill() -> int:
    return main([])


if __name__ == "__main__":
    raise SystemExit(main())
