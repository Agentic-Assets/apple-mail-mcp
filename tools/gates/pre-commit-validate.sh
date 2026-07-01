#!/usr/bin/env bash
# Git pre-commit hook — manifest drift + mocked pytest (+ wrapper when tool surface staged).
# Also runs ruff on staged Python files (fast, no full lint tier overhead).
# Install once per clone: bash tools/gates/install-git-hooks.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

RUFF="${ROOT}/.venv/bin/ruff"

# Lint staged Python files with ruff (warn-only baseline — pre-existing UP0xx
# modernization debt is non-blocking until a dedicated sweep PR lands).
STAGED_PY=$(git diff --cached --name-only --diff-filter=ACMR | grep -E '\.py$' || true)
if [[ -n "$STAGED_PY" ]] && [[ -x "$RUFF" ]]; then
  echo "→ ruff check (staged files, warn-only)"
  echo "$STAGED_PY" | xargs "$RUFF" check || echo "warning: ruff reported errors in staged files (non-blocking baseline)"
fi

bash tools/gates/dev-check.sh default
