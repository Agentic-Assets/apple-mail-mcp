#!/usr/bin/env bash
# Unified local dev gate — manifests, pytest, optional wrapper surface check.
#
# Tiers:
#   default  — validate_manifests + pytest; wrapper check when staged tool surface changes
#   lint     — ruff check + ruff format --check + mypy (warn-only on mypy)
#   surface  — default + check_wrapper_surface.py (skips if no wrapper on PATH)
#   manifest — validate_manifests.sh only
#   live     — default + quick-check against Mail.app (macOS, explicit)
#   release  — lint + rebuild apple-mail-plugin.zip + .mcpb + APPLE_MAIL_REQUIRE_DIST_ARTIFACTS validate + pytest + wrapper (run before commit/PR)
#   all      — default + wrapper check always
#
# Usage:
#   bash tools/dev-check.sh
#   bash tools/dev-check.sh lint
#   bash tools/dev-check.sh surface
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
PYTEST="${ROOT}/.venv/bin/pytest"
RUFF="${ROOT}/.venv/bin/ruff"
MYPY="${ROOT}/.venv/bin/mypy"
CLI="${ROOT}/.venv/bin/apple-mail"

TIER="${1:-default}"

if [[ ! -x "$PYTEST" ]]; then
  echo "error: missing .venv — run: python3 -m venv .venv && .venv/bin/pip install -e . pytest" >&2
  exit 1
fi

run_manifests() {
  bash tools/validate_manifests.sh
}

run_pytest() {
  "$PYTEST" tests/ -q
}

run_wrapper() {
  "$PY" tools/check_wrapper_surface.py
}

run_lint() {
  local lint_ok=0

  if [[ ! -x "$RUFF" ]]; then
    echo "warning: ruff not found in .venv — run: .venv/bin/pip install ruff" >&2
  else
    echo "→ ruff check"
    "$RUFF" check plugin/ tools/ tests/ || lint_ok=1
    echo "→ ruff format --check"
    "$RUFF" format --check plugin/ tools/ tests/ || lint_ok=1
  fi

  if [[ ! -x "$MYPY" ]]; then
    echo "warning: mypy not found in .venv — run: .venv/bin/pip install mypy" >&2
  else
    echo "→ mypy (warn-only baseline)"
    "$MYPY" plugin/apple_mail_mcp/ || echo "warning: mypy reported errors (non-blocking — tighten baseline before making fatal)"
  fi

  if [[ $lint_ok -ne 0 ]]; then
    echo "lint: FAILED (ruff errors above must be fixed)" >&2
    exit 1
  fi
  echo "lint: OK"
}

staged_touches_tool_surface() {
  git diff --cached --name-only 2>/dev/null | grep -Eq \
    '^plugin/apple_mail_mcp/tools/|^plugin/apple_mail_mcp/__init__\.py|^plugin/apple_mail_mcp/server\.py|^apple-mail-mcpb/manifest\.json'
}

run_default() {
  run_manifests
  run_pytest
}

maybe_run_wrapper_for_staged_surface() {
  if staged_touches_tool_surface; then
    echo "→ staged MCP tool surface changes detected; running wrapper check"
    run_wrapper
  fi
}

case "$TIER" in
  default)
    run_default
    maybe_run_wrapper_for_staged_surface
    ;;
  lint)
    run_lint
    ;;
  surface)
    run_default
    run_wrapper
    ;;
  manifest)
    run_manifests
    ;;
  live)
    run_default
    if [[ ! -x "$CLI" ]]; then
      echo "error: missing repo CLI at .venv/bin/apple-mail" >&2
      exit 1
    fi
    "$CLI" quick-check --json
    ;;
  all)
    run_default
    run_wrapper
    ;;
  release)
    run_lint
    bash tools/build-artifacts.sh
    run_pytest
    run_wrapper
    ;;
  *)
    echo "Usage: bash tools/dev-check.sh [default|surface|manifest|lint|live|release|all]" >&2
    exit 2
    ;;
esac
