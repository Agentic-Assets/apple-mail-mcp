#!/usr/bin/env bash
# Verify an unpacked plugin or release archive can create its runtime offline.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INPUT="${1:-$ROOT/plugin}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/apple-mail-offline.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

if [[ -d "$INPUT" ]]; then
  cp -R "$INPUT" "$WORK/plugin"
elif [[ -f "$INPUT" ]]; then
  mkdir -p "$WORK/plugin"
  unzip -q "$INPUT" -d "$WORK/plugin"
else
  echo "error: offline runtime input not found: $INPUT" >&2
  exit 1
fi

cd "$WORK/plugin"
PIP_NO_INDEX=1 ./start_mcp.sh --doctor
test -x venv/bin/python3
venv/bin/python3 -c 'import fastmcp, mcp_ui_server'
echo "offline runtime verified: $INPUT"
