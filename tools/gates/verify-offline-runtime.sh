#!/usr/bin/env bash
# Verify offline bootstrap and the MCP version advertised by the bundled launcher.
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
manifest=".claude-plugin/plugin.json"
if [[ -f manifest.json ]]; then
  manifest="manifest.json"
fi
expected_version="$(venv/bin/python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "$manifest")"
venv/bin/python3 "$ROOT/tools/probes/mcp_tool_smoke.py" \
  --command /bin/bash \
  --arg ./start_mcp.sh \
  --arg=--draft-safe \
  --cwd "$WORK/plugin" \
  --timeout 45 \
  --required-tool export_emails \
  --expect-version "$expected_version"
echo "offline runtime verified: $INPUT"
