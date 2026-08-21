#!/usr/bin/env bash
# Verify a signed release tag and print the exact Marketplace handoff facts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/tools/release/marketplace_handoff.py" "$@"
