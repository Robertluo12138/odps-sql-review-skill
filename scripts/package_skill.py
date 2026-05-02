#!/usr/bin/env python3
"""scripts/package_skill.py

Build the three distributable zip archives for ``odps-sql-review-skill``:

1. ``dist/odps-sql-review-qoderwork-flat.zip``
   - Flat zip with ``SKILL.md`` directly at the root, no top-level
     folder.  Primary artifact for QoderWork and similar AI tools that
     auto-detect a skill from the archive root.
   - Contains: ``SKILL.md``, ``references/``, ``examples/``,
     ``templates/``.

2. ``dist/odps-sql-review-qoderwork-folder.zip``
   - Same content as (1) but wrapped inside a top-level folder
     ``odps-sql-review/``.  Use this when QoderWork (or any other
     skill loader) expects the skill folder to be present inside the
     zip.

3. ``dist/odps-sql-review-full-repo.zip``
   - The full repository (skill + optional Python CLI + docs +
     packaging scripts).  Useful for GitHub releases or for users who
     want both the skill and the optional Python static checker.

Privacy guarantees for the full-repo zip
----------------------------------------

The full-repo target deliberately walks the working tree, so it is at
risk of including locally created files that match the project's
``.gitignore`` (e.g. ``company_context/*.local.yml``,
``private_cases/``, ``acceptance_reports/``).  Three independent
layers of protection are applied:

* If the working tree is a git repo, ``git ls-files`` is preferred so
  only tracked files are packaged - any file ignored by
  ``.gitignore`` is automatically excluded.
* A hard-coded denylist of private directory names and filename
  patterns is applied as a fallback (and as defense-in-depth even when
  ``git ls-files`` succeeds).
* A final scan refuses to write the zip if any file path matches a
  private pattern; the script aborts with a clear error.

The script never executes SQL and never connects to ODPS.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile
from typing import Iterable, List, Set, Tuple


# --------------------------------------------------------------------- #
# Exclusion lists                                                       #
# --------------------------------------------------------------------- #

# Generic build / cache / vcs directories that should never appear in
# any distributable zip.
_DEFAULT_EXCLUDE_DIRS: Set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".git",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "node_modules",
    ".venv",
    "venv",
    "env",
}

_DEFAULT_EXCLUDE_FILES: Set[str] = {
    ".DS_Store",
    "Thumbs.db",
}

# Private business-context directories.  These mirror the patterns in
# ``.gitignore`` so the full-repo zip is safe even when the user's
# working copy contains local company SQL / table profiles / acceptance
# reports.  We exclude the directories regardless of whether git is
# available.
_PRIVATE_DIR_NAMES: Set[str] = {
    "company_context",
    "private_cases",
    "private_profiles",
    "acceptance_reports",
}

# Private filename patterns (compiled regex).  Tested against the
# *basename* of every candidate file.
_PRIVATE_FILE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r".*\.local\.ya?ml$", re.IGNORECASE),
    re.compile(r".*\.local\.md$", re.IGNORECASE),
    re.compile(r"^private_.*\.sql$", re.IGNORECASE),
)


# --------------------------------------------------------------------- #
# Privacy helpers                                                       #
# --------------------------------------------------------------------- #


def _is_private_path(repo_relative_path: str) -> bool:
    """Return True when a repo-relative path matches a private rule.

    ``repo_relative_path`` is checked against:
      - the private directory denylist (anywhere in the path);
      - the private filename pattern denylist (basename only).
    """

    # Normalize separators so the same logic works on Windows and Unix.
    norm = repo_relative_path.replace(os.sep, "/")
    parts = norm.split("/")
    for part in parts[:-1]:  # directory components only
        if part in _PRIVATE_DIR_NAMES:
            return True
    basename = parts[-1] if parts else ""
    for pattern in _PRIVATE_FILE_PATTERNS:
        if pattern.match(basename):
            return True
    return False


def _scan_for_private_leaks(
    files: Iterable[Tuple[str, str]],
    repo_root: str,
) -> List[str]:
    """Return repo-relative paths that match any private rule.

    Used as the final defense-in-depth check before writing the zip.
    """

    leaks: List[str] = []
    for source_path, _arcname in files:
        # Use the on-disk path relative to repo root; arcname may have
        # been rewritten (e.g. for the folder-wrapped QoderWork zip)
        # but the source path tells us where the file really lives.
        try:
            rel = os.path.relpath(source_path, repo_root)
        except ValueError:
            rel = source_path
        if _is_private_path(rel):
            leaks.append(rel)
    return leaks


# --------------------------------------------------------------------- #
# File enumeration                                                      #
# --------------------------------------------------------------------- #


def _iter_files(root: str, extra_excludes: Iterable[str] = ()) -> List[str]:
    """Walk ``root`` and yield every file path that should be packaged.

    The directory denylist combines the generic build/cache directories
    with the private business-context directories so a non-git
    invocation is still safe.
    """

    excluded_dirs = _DEFAULT_EXCLUDE_DIRS | _PRIVATE_DIR_NAMES | set(extra_excludes)
    selected: List[str] = []
    for current, dirs, files in os.walk(root):
        # Mutate ``dirs`` in place so os.walk skips excluded directories.
        dirs[:] = [
            d
            for d in dirs
            if d not in excluded_dirs and not d.endswith(".egg-info")
        ]
        for name in files:
            if name in _DEFAULT_EXCLUDE_FILES:
                continue
            if name.endswith((".pyc", ".pyo")):
                continue
            full_path = os.path.join(current, name)
            # Apply private-file pattern denylist on basenames.
            if any(p.match(name) for p in _PRIVATE_FILE_PATTERNS):
                continue
            selected.append(full_path)
    return sorted(selected)


def _git_ls_files(repo_root: str) -> List[str] | None:
    """Return repo files filtered through ``.gitignore``, or None if unavailable.

    Uses ``git ls-files --cached --others --exclude-standard`` so the
    output includes:
      - tracked files (``--cached``);
      - new files that have not been committed yet (``--others``);
      - while still respecting ``.gitignore`` / global excludes
        (``--exclude-standard``).

    This way the full-repo zip stays in sync with the working copy
    (it picks up newly added files) but never includes anything that
    matches ``.gitignore``.

    Returns ``None`` when the working copy is not a git repo or git is
    missing, so the caller can fall back to the plain walk.
    """

    git_dir = os.path.join(repo_root, ".git")
    if not os.path.isdir(git_dir) and not os.path.isfile(git_dir):
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    raw = result.stdout.decode("utf-8", errors="replace")
    if not raw:
        return []
    rels = [r for r in raw.split("\0") if r]
    out: List[str] = []
    for rel in rels:
        full = os.path.join(repo_root, rel)
        if not os.path.isfile(full):
            continue
        out.append(full)
    return sorted(out)


# --------------------------------------------------------------------- #
# Misc helpers                                                          #
# --------------------------------------------------------------------- #


def _ensure_dir(path: str) -> None:
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def _write_zip(
    files: List[Tuple[str, str]],
    output_path: str,
) -> None:
    """Write ``files`` (pairs of (source_path, arcname)) to a zip archive."""

    if os.path.exists(output_path):
        os.remove(output_path)
    _ensure_dir(os.path.dirname(output_path))
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source_path, arcname in files:
            zf.write(source_path, arcname)


def _summarise(zip_path: str) -> None:
    """Print a one-liner summary of a zip's root entries."""

    if not os.path.exists(zip_path):
        print(f"  [skip] {zip_path} (missing)")
        return
    size_kb = os.path.getsize(zip_path) / 1024
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        roots = sorted({name.split("/", 1)[0] for name in names})
    print(f"  -> {zip_path}  ({size_kb:.1f} KB, {len(names)} files)")
    print(f"     archive root entries: {', '.join(roots)}")


# --------------------------------------------------------------------- #
# The three build targets                                               #
# --------------------------------------------------------------------- #


def _build_skill_files(repo_root: str) -> List[str]:
    """Enumerate files inside the pure skill package."""

    skill_root = os.path.join(repo_root, "skill_package", "odps-sql-review")
    if not os.path.isdir(skill_root):
        raise SystemExit(
            f"[package_skill] error: skill_package/odps-sql-review not found at {skill_root}."
        )
    if not os.path.exists(os.path.join(skill_root, "SKILL.md")):
        raise SystemExit(
            f"[package_skill] error: SKILL.md missing inside {skill_root}."
        )
    return _iter_files(skill_root)


def build_qoderwork_flat(repo_root: str, output_path: str) -> None:
    """SKILL.md sits at the zip root.  Primary QoderWork artifact."""

    skill_root = os.path.join(repo_root, "skill_package", "odps-sql-review")
    files: List[Tuple[str, str]] = []
    for source_path in _build_skill_files(repo_root):
        arcname = os.path.relpath(source_path, skill_root)
        files.append((source_path, arcname))

    leaks = _scan_for_private_leaks(files, repo_root)
    if leaks:
        raise SystemExit(_format_leak_error("qoderwork-flat", leaks))
    _write_zip(files, output_path)


def build_qoderwork_folder(repo_root: str, output_path: str) -> None:
    """Same content as flat, but wrapped in odps-sql-review/ folder."""

    skill_root = os.path.join(repo_root, "skill_package", "odps-sql-review")
    files: List[Tuple[str, str]] = []
    for source_path in _build_skill_files(repo_root):
        relative = os.path.relpath(source_path, skill_root)
        arcname = os.path.join("odps-sql-review", relative)
        files.append((source_path, arcname))

    leaks = _scan_for_private_leaks(files, repo_root)
    if leaks:
        raise SystemExit(_format_leak_error("qoderwork-folder", leaks))
    _write_zip(files, output_path)


def build_full_repo(repo_root: str, output_path: str) -> None:
    """Whole repository (skill + Python CLI + docs + scripts + tests).

    Honors ``.gitignore`` via ``git ls-files`` when available.  Falls
    back to a directory walk that still applies the hard-coded private
    denylist.  A final scan refuses to write the zip if any private
    file pattern slipped through.
    """

    sources = _git_ls_files(repo_root)
    used_git = sources is not None
    if not used_git:
        sources = _iter_files(repo_root, extra_excludes={"dist"})

    files: List[Tuple[str, str]] = []
    for source_path in sources:
        arcname = os.path.relpath(source_path, repo_root)
        # Drop dist/* explicitly so prior builds are not bundled, even
        # when they were temporarily git-tracked.
        if arcname == "dist" or arcname.startswith("dist" + os.sep):
            continue
        # Apply the private-pattern denylist again as defense-in-depth.
        if _is_private_path(arcname):
            continue
        # And the basename file pattern denylist.
        basename = os.path.basename(arcname)
        if any(p.match(basename) for p in _PRIVATE_FILE_PATTERNS):
            continue
        # Drop generic excluded files.
        if basename in _DEFAULT_EXCLUDE_FILES:
            continue
        if basename.endswith((".pyc", ".pyo")):
            continue
        files.append((source_path, arcname))

    if used_git:
        print("  [info] using `git ls-files` (.gitignore honored).")
    else:
        print(
            "  [info] git not available - falling back to filtered walk. "
            "Anything matching the private denylist is excluded."
        )

    leaks = _scan_for_private_leaks(files, repo_root)
    if leaks:
        raise SystemExit(_format_leak_error("full-repo", leaks))
    _write_zip(files, output_path)


def _format_leak_error(target: str, leaks: List[str]) -> str:
    sample = "\n  - ".join(leaks[:20])
    more = f"\n  ... and {len(leaks) - 20} more" if len(leaks) > 20 else ""
    return (
        f"[package_skill] error: refusing to build {target}: "
        f"{len(leaks)} private file(s) would be included.\n"
        f"  - {sample}{more}\n"
        f"Move these files outside the repo or rename them so they no longer match "
        f"the private denylist (company_context/, private_cases/, private_profiles/, "
        f"acceptance_reports/, *.local.yml, *.local.md, private_*.sql)."
    )


# --------------------------------------------------------------------- #
# CLI                                                                   #
# --------------------------------------------------------------------- #


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the three distributable zip archives for odps-sql-review-skill.",
    )
    parser.add_argument(
        "--repo-root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Root of the repository (default: parent of this script).",
    )
    parser.add_argument(
        "--dist-dir",
        default=None,
        help="Directory to write zips into (default: <repo_root>/dist).",
    )
    parser.add_argument(
        "--only",
        choices=("flat", "folder", "full", "all"),
        default="all",
        help="Build a single artifact instead of all three (default: all).",
    )
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    dist_dir = args.dist_dir or os.path.join(repo_root, "dist")
    _ensure_dir(dist_dir)

    flat_zip = os.path.join(dist_dir, "odps-sql-review-qoderwork-flat.zip")
    folder_zip = os.path.join(dist_dir, "odps-sql-review-qoderwork-folder.zip")
    full_zip = os.path.join(dist_dir, "odps-sql-review-full-repo.zip")

    print(f"[package_skill] repo root: {repo_root}")
    print(f"[package_skill] dist dir : {dist_dir}")
    print()

    if args.only in ("flat", "all"):
        print("[package_skill] building qoderwork-flat ...")
        build_qoderwork_flat(repo_root, flat_zip)
        _summarise(flat_zip)
        print()
    if args.only in ("folder", "all"):
        print("[package_skill] building qoderwork-folder ...")
        build_qoderwork_folder(repo_root, folder_zip)
        _summarise(folder_zip)
        print()
    if args.only in ("full", "all"):
        print("[package_skill] building full-repo ...")
        build_full_repo(repo_root, full_zip)
        _summarise(full_zip)
        print()

    print("[package_skill] done.")
    print()
    print("Tips:")
    print("  - First try importing odps-sql-review-qoderwork-flat.zip into QoderWork.")
    print("  - If QoderWork expects a folder inside the zip, fall back to")
    print("    odps-sql-review-qoderwork-folder.zip.")
    print("  - odps-sql-review-full-repo.zip ships the optional Python CLI as well.")
    return 0


# Allow ``import scripts.package_skill`` and call ``package_skill()``.
def package_skill() -> int:
    return main([])


if __name__ == "__main__":
    raise SystemExit(main())
