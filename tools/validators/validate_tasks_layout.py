#!/usr/bin/env python3
"""Enforce tasks/ folder layout for cross-session agent handoffs.

Layout contract (see tasks/CLAUDE.md § Agent requirements):
  tasks/
    CLAUDE.md, INDEX.md, todo.md   — navigation only at root
    active/                        — open workstreams
    reference/                     — durable specs cited by code/docs
    archive/                       — shipped, superseded, or resolved artifacts

Exit 0 when compliant; exit 1 and print violations to stderr otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks"

ALLOWED_ROOT_FILES = frozenset({"CLAUDE.md", "INDEX.md", "todo.md"})
ALLOWED_ROOT_DIRS = frozenset({"active", "reference", "archive"})

CLAUDE_REQUIRED_MARKERS = (
    "## Agent requirements",
    "`active/`",
    "`reference/`",
    "`archive/`",
    "tasks/todo.md",
    "tasks/INDEX.md",
)

INDEX_REQUIRED_MARKERS = (
    "[`active/`](active/)",
    "[`reference/`](reference/)",
    "[`archive/`](archive/)",
)

# Flat-root task paths that must not reappear after the 2026-06-30 reorganize.
STALE_FLAT_PATH_RE = re.compile(
    r"tasks/(?!active/|reference/|archive/|todo\.md|INDEX\.md|CLAUDE\.md)"
    r"[A-Za-z0-9_.-]+\.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def validate_tasks_layout() -> list[str]:
    errors: list[str] = []

    if not TASKS.is_dir():
        return [f"missing tasks directory: {TASKS}"]

    for child in TASKS.iterdir():
        name = child.name
        if child.is_file():
            if name.endswith(".md") and name not in ALLOWED_ROOT_FILES:
                errors.append(
                    f"loose markdown at tasks root: {name} "
                    f"(move under active/, reference/, or archive/)"
                )
            elif not name.endswith(".md") and name not in ALLOWED_ROOT_FILES:
                errors.append(f"unexpected file at tasks root: {name}")
        elif child.is_dir() and name not in ALLOWED_ROOT_DIRS:
            errors.append(
                f"unexpected directory at tasks root: {name}/ "
                f"(use active/, reference/, or archive/)"
            )

    for required_dir in ALLOWED_ROOT_DIRS:
        if not (TASKS / required_dir).is_dir():
            errors.append(f"missing required directory: tasks/{required_dir}/")

    claude = TASKS / "CLAUDE.md"
    if claude.is_file():
        text = _read(claude)
        for marker in CLAUDE_REQUIRED_MARKERS:
            if marker not in text:
                errors.append(f"tasks/CLAUDE.md missing required marker: {marker!r}")
    else:
        errors.append("missing tasks/CLAUDE.md")

    index = TASKS / "INDEX.md"
    if index.is_file():
        text = _read(index)
        for marker in INDEX_REQUIRED_MARKERS:
            if marker not in text:
                errors.append(f"tasks/INDEX.md missing required marker: {marker!r}")
    else:
        errors.append("missing tasks/INDEX.md")

    todo = TASKS / "todo.md"
    if todo.is_file():
        todo_text = _read(todo)
        for match in STALE_FLAT_PATH_RE.finditer(todo_text):
            errors.append(
                f"tasks/todo.md uses stale flat path {match.group(0)!r} "
                f"(use tasks/active/, tasks/reference/, or tasks/archive/)"
            )
    else:
        errors.append("missing tasks/todo.md")

    archive_readme = TASKS / "archive" / "README.md"
    if not archive_readme.is_file():
        errors.append("missing tasks/archive/README.md")

    return errors


def main() -> int:
    errors = validate_tasks_layout()
    if errors:
        print("tasks layout validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("tasks layout: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
