#!/usr/bin/env bash
# Full local blocker for release-sensitive source changes. No GitHub Actions.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

bash tools/gates/dev-check.sh release
"$ROOT/.venv/bin/python" tools/release/source_release.py validate-tree HEAD
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "error: release gate left tracked or index drift; review and commit the exact release tree before stamping" >&2
  exit 1
fi
"$ROOT/.venv/bin/python" tools/release/pre_push.py stamp --root "$ROOT"
