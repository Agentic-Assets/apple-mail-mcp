#!/usr/bin/env bash
# Install the checked-in pre-commit and pre-push local blockers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "error: $ROOT is not a git checkout" >&2
  exit 1
fi

chmod +x \
  "$ROOT/.githooks/pre-commit" \
  "$ROOT/.githooks/pre-push" \
  "$ROOT/tools/gates/create-release-tag.sh" \
  "$ROOT/tools/gates/dev-check.sh" \
  "$ROOT/tools/gates/install-git-hooks.sh" \
  "$ROOT/tools/gates/pre-commit-validate.sh" \
  "$ROOT/tools/gates/source-release-gate.sh"
git -C "$ROOT" config core.hooksPath .githooks
INSTALLED_HOOKS_PATH="$(git -C "$ROOT" config --get core.hooksPath || true)"
if [[ "$INSTALLED_HOOKS_PATH" != ".githooks" ]]; then
  echo "error: core.hooksPath readback failed (found: ${INSTALLED_HOOKS_PATH:-<unset>})" >&2
  exit 1
fi

echo "Installed checked-in Git hooks from .githooks/:"
echo "  pre-commit -> tools/gates/pre-commit-validate.sh"
echo "  pre-push   -> release-sensitive change detector and current local gate stamp"
echo ""
echo "Each commit runs: bash tools/gates/dev-check.sh default"
echo "  • validate_manifests.sh + pytest (always)"
echo "  • check_wrapper_surface.py when staged files touch MCP tool surface"
echo ""
echo "Before a release-sensitive push: bash tools/gates/source-release-gate.sh"
echo "Verified: core.hooksPath=.githooks"
