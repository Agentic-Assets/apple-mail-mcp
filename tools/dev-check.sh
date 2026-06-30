#!/usr/bin/env bash
# Unified local dev gate — manifests, pytest, optional wrapper surface check.
#
# Tiers:
#   default  — validate_manifests + pytest; wrapper check when staged tool surface changes
#   lint     — strict package gate: ruff check + ruff format --check + mypy --strict
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

# Single source of truth for the collected-test count: tools/expected_test_count.txt.
# Docs no longer hardcode the number; this gate recomputes it and fails on drift,
# telling you the one line to update. Mirrors the documented recount command.
run_test_count_check() {
  local count_file="${ROOT}/tools/expected_test_count.txt"
  if [[ ! -f "$count_file" ]]; then
    echo "error: missing ${count_file} (single source of truth for collected test count)" >&2
    exit 1
  fi
  local expected actual
  expected="$(tr -d '[:space:]' < "$count_file")"
  actual="$(PYTEST_ADDOPTS='' "$PYTEST" --collect-only tests 2>/dev/null \
    | grep -oE '[0-9]+ tests collected' | grep -oE '[0-9]+' | head -1)"
  if [[ -z "$actual" ]]; then
    echo "error: could not determine collected test count from pytest --collect-only" >&2
    exit 1
  fi
  if [[ "$expected" != "$actual" ]]; then
    echo "test-count drift: tools/expected_test_count.txt says ${expected}, actual collected ${actual}" >&2
    echo "  -> update tools/expected_test_count.txt to ${actual}" >&2
    exit 1
  fi
  echo "test count: OK (${actual} collected, matches tools/expected_test_count.txt)"
}

run_lint() {
  if [[ ! -x "$RUFF" ]]; then
    echo "error: ruff not found in .venv — run: .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
  fi

  if [[ ! -x "$MYPY" ]]; then
    echo "error: mypy not found in .venv — run: .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
  fi

  echo "→ ruff check plugin/apple_mail_mcp/"
  "$RUFF" check plugin/apple_mail_mcp/
  echo "→ ruff format --check plugin/apple_mail_mcp/"
  "$RUFF" format --check plugin/apple_mail_mcp/
  echo "→ mypy --strict plugin/apple_mail_mcp/"
  "$MYPY" --strict plugin/apple_mail_mcp/

  echo "lint: OK"
}

staged_touches_tool_surface() {
  git diff --cached --name-only 2>/dev/null | grep -Eq \
    '^plugin/apple_mail_mcp/tools/|^plugin/apple_mail_mcp/__init__\.py|^plugin/apple_mail_mcp/server\.py|^apple-mail-mcpb/manifest\.json'
}

run_default() {
  run_manifests
  run_pytest
  run_test_count_check
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
    run_test_count_check
    run_wrapper
    ;;
  *)
    echo "Usage: bash tools/dev-check.sh [default|surface|manifest|lint|live|release|all]" >&2
    exit 2
    ;;
esac
