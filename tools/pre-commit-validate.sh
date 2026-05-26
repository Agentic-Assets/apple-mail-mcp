#!/usr/bin/env bash
# Git pre-commit hook — manifest drift + mocked pytest (+ wrapper when tool surface staged).
# Also runs ruff on staged Python files (fast, no full lint tier overhead).
# Install once per clone: bash tools/install-git-hooks.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

RUFF="${ROOT}/.venv/bin/ruff"

# Lint staged Python files with ruff (errors are fatal; format check is advisory)
STAGED_PY=$(git diff --cached --name-only --diff-filter=ACMR | grep -E '\.py$' || true)
if [[ -n "$STAGED_PY" ]] && [[ -x "$RUFF" ]]; then
  echo "→ ruff check (staged files)"
  echo "$STAGED_PY" | xargs "$RUFF" check || {
    echo "pre-commit: ruff found errors in staged files — fix before committing" >&2
    exit 1
  }
fi

bash tools/dev-check.sh default
