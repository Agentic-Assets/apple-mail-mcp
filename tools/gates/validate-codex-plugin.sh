#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SMOKE_PYTHON="${APPLE_MAIL_MCP_SMOKE_PYTHON:-$ROOT/.venv/bin/python}"
REQUIRED_TOOLS=(
  reply_to_email
  compose_email
  manage_drafts
  list_accounts
  get_inbox_overview
)
SMOKE_TOOL_ARGS=()
for tool_name in "${REQUIRED_TOOLS[@]}"; do
  SMOKE_TOOL_ARGS+=(--required-tool "$tool_name")
done

# Derive the expected tool count from the @mcp.tool decorators so this gate
# stays correct as tools are added, mirroring validate_manifests.sh. The
# compose/ group is a package, so recurse instead of scanning one level.
EXPECTED_TOOL_COUNT="$({ rg "^@mcp\.tool" -g '*.py' plugin/apple_mail_mcp/tools || true; } | wc -l | tr -d " ")"
if [ -z "$EXPECTED_TOOL_COUNT" ] || [ "$EXPECTED_TOOL_COUNT" -eq 0 ]; then
  echo "error: no @mcp.tool registrations found under plugin/apple_mail_mcp/tools/" >&2
  exit 1
fi
SMOKE_TOOL_ARGS+=(--expect-count "$EXPECTED_TOOL_COUNT")

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not on PATH; skipping Codex plugin install smoke"
  exit 0
fi

if ! codex plugin --help >/dev/null 2>&1; then
  echo "codex CLI does not expose plugin commands; skipping Codex plugin install smoke"
  exit 0
fi

if [ ! -x "$SMOKE_PYTHON" ]; then
  echo "MCP smoke Python not executable: $SMOKE_PYTHON" >&2
  echo "Create the repo dev venv first: python3 -m venv .venv && .venv/bin/pip install -e . pytest" >&2
  exit 1
fi

TMP_HOME="$(mktemp -d)"
trap 'rm -rf "$TMP_HOME"' EXIT
IFS=$'\t' read -r CODEX_MARKETPLACE_NAME CODEX_PLUGIN_SELECTOR < <("$SMOKE_PYTHON" - <<'PY'
import json

identity = json.load(open("tools/marketplace_identity.json", encoding="utf-8"))
standalone = identity["standalone_compatibility"]
print(standalone["marketplace_id"], standalone["selector"], sep="\t")
PY
)
# Release candidates must be tested from the checkout that built the artifacts.
# Set APPLE_MAIL_CODEX_MARKETPLACE_SOURCE explicitly only when checking a
# published marketplace snapshot.
CODEX_MARKETPLACE_SOURCE="${APPLE_MAIL_CODEX_MARKETPLACE_SOURCE:-$ROOT}"

"$SMOKE_PYTHON" tools/probes/mcp_tool_smoke.py \
  --command /bin/bash \
  --arg "$ROOT/plugin/start_mcp.sh" \
  --arg=--draft-safe \
  --cwd "$ROOT" \
  "${SMOKE_TOOL_ARGS[@]}"

export CODEX_HOME="$TMP_HOME"

codex plugin marketplace add "$CODEX_MARKETPLACE_SOURCE"
codex plugin add "$CODEX_PLUGIN_SELECTOR"
codex plugin list --marketplace "$CODEX_MARKETPLACE_NAME" | grep -F "$CODEX_PLUGIN_SELECTOR" >/dev/null

SERVER_JSON="$TMP_HOME/apple-mail-mcp-server.json"
codex mcp get apple-mail --json > "$SERVER_JSON"

"$SMOKE_PYTHON" tools/probes/mcp_tool_smoke.py \
  --server-json "$SERVER_JSON" \
  --reject-literal '${CLAUDE_PLUGIN_ROOT}' \
  "${SMOKE_TOOL_ARGS[@]}"

echo "Codex plugin install + MCP runtime smoke OK"
