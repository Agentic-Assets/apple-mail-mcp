#!/usr/bin/env bash
# Safe wrapper: preflight by default; creation requires an explicit confirmation flag.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ $# -gt 2 || ( $# -eq 2 && "${1:-}" != "--confirm-create" ) ]]; then
  echo "usage: $0 [--confirm-create] [tag]" >&2
  exit 2
fi

if [[ "${1:-}" == "--confirm-create" ]]; then
  shift
  if [[ $# -gt 0 ]]; then
    exec "$ROOT/.venv/bin/python" tools/release/source_release.py create-tag "$1" --confirm-create
  fi
  exec "$ROOT/.venv/bin/python" tools/release/source_release.py create-tag --confirm-create
fi

if [[ $# -gt 0 ]]; then
  exec "$ROOT/.venv/bin/python" tools/release/source_release.py preflight-create "$1"
fi
exec "$ROOT/.venv/bin/python" tools/release/source_release.py preflight-create
