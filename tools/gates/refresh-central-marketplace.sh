#!/usr/bin/env bash
# Install or refresh Apple Mail from the shared Agentic Assets marketplace.
# This helper never removes a marketplace, plugin, cache, or user data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

MODE="${1:-apply}"
if [[ "$MODE" != "apply" && "$MODE" != "--check" ]]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

for command_name in python3 claude codex; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "error: required command is unavailable: ${command_name}" >&2
    exit 1
  fi
done

IDENTITY_JSON="$ROOT/tools/marketplace_identity.json"
if [[ ! -f "$IDENTITY_JSON" ]]; then
  echo "error: missing marketplace identity contract: $IDENTITY_JSON" >&2
  exit 1
fi

IFS=$'\t' read -r MARKETPLACE_NAME PLUGIN_SELECTOR MARKETPLACE_URL < <(python3 - "$IDENTITY_JSON" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
central = data["primary_marketplace"]
print(central["id"], central["selector"], central["repository"], sep="\t")
PY
)
MARKETPLACE_REPO="${MARKETPLACE_URL#https://github.com/}"
MARKETPLACE_REPO="${MARKETPLACE_REPO%.git}"
MARKETPLACE_URL="${MARKETPLACE_URL%.git}.git"

python3 tools/validators/validate_marketplace_payload.py >/dev/null

STATE_DIR="$(mktemp -d)"
trap 'rm -rf "$STATE_DIR"' EXIT
claude plugin marketplace list --json >"$STATE_DIR/claude-marketplaces.json"
codex plugin marketplace list --json >"$STATE_DIR/codex-marketplaces.json"
claude plugin list --json >"$STATE_DIR/claude-plugins-before.json"

python3 - "$STATE_DIR" "$MARKETPLACE_NAME" "$MARKETPLACE_URL" "$PLUGIN_SELECTOR" <<'PY'
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

state = Path(sys.argv[1])
target = sys.argv[2]
expected = sys.argv[3]
selector = sys.argv[4]


def normalized(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if "://" in text:
        parsed = urlparse(text)
        text = f"{parsed.netloc}{parsed.path}"
    return text.lower().removeprefix("github.com/")


expected_normalized = normalized(expected)
claude_rows = json.loads((state / "claude-marketplaces.json").read_text())
codex_payload = json.loads((state / "codex-marketplaces.json").read_text())
codex_rows = codex_payload.get("marketplaces", [])
claude_plugins = json.loads((state / "claude-plugins-before.json").read_text())

problems = []
claude_present = False
codex_present = False
for row in claude_rows:
    if row.get("name") != target:
        continue
    claude_present = True
    source = row.get("repo") or row.get("source")
    if normalized(source) != expected_normalized:
        problems.append(f"Claude marketplace '{target}' points to an unapproved source: {source!r}")
for row in codex_rows:
    if row.get("name") != target:
        continue
    codex_present = True
    source = (row.get("marketplaceSource") or {}).get("source") or row.get("source")
    if normalized(source) != expected_normalized:
        problems.append(f"Codex marketplace '{target}' points to an unapproved source: {source!r}")

installed_scopes = [str(row.get("scope") or "").strip() for row in claude_plugins if row.get("id") == selector]
if any(scope not in {"user", "project", "local"} for scope in installed_scopes):
    problems.append(
        f"Claude plugin '{selector}' has an unknown installed scope: {installed_scopes!r}"
    )
if len(installed_scopes) > 1:
    problems.append(
        f"Claude plugin '{selector}' is installed more than once across scopes: {installed_scopes!r}"
    )

if problems:
    print("error: central marketplace preflight refused:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("No marketplace or plugin registrations were changed.", file=sys.stderr)
    raise SystemExit(1)

(state / "presence.json").write_text(
    json.dumps(
        {
            "claude": claude_present,
            "codex": codex_present,
            "claude_plugin_scope": installed_scopes[0] if installed_scopes else None,
        }
    ),
    encoding="utf-8",
)
PY

if [[ "$MODE" == "--check" ]]; then
  echo "Central marketplace preflight passed for ${PLUGIN_SELECTOR}. No registrations changed."
  exit 0
fi

CLAUDE_PRESENT="$(python3 -c 'import json,sys; print("1" if json.load(open(sys.argv[1]))["claude"] else "0")' "$STATE_DIR/presence.json")"
CODEX_PRESENT="$(python3 -c 'import json,sys; print("1" if json.load(open(sys.argv[1]))["codex"] else "0")' "$STATE_DIR/presence.json")"
CLAUDE_PLUGIN_SCOPE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["claude_plugin_scope"] or "")' "$STATE_DIR/presence.json")"

if [[ "$CLAUDE_PRESENT" == "1" ]]; then
  claude plugin marketplace update "$MARKETPLACE_NAME"
else
  claude plugin marketplace add "$MARKETPLACE_REPO" --scope user
  claude plugin marketplace update "$MARKETPLACE_NAME"
fi
if [[ -n "$CLAUDE_PLUGIN_SCOPE" ]]; then
  claude plugin update "$PLUGIN_SELECTOR" --scope "$CLAUDE_PLUGIN_SCOPE"
else
  claude plugin install "$PLUGIN_SELECTOR" --scope user
  CLAUDE_PLUGIN_SCOPE="user"
fi

if [[ "$CODEX_PRESENT" == "1" ]]; then
  codex plugin marketplace upgrade "$MARKETPLACE_NAME"
else
  codex plugin marketplace add "$MARKETPLACE_URL"
  codex plugin marketplace upgrade "$MARKETPLACE_NAME"
fi
codex plugin add "$PLUGIN_SELECTOR"

claude plugin list --json >"$STATE_DIR/claude-plugins-after.json"
codex plugin list --json >"$STATE_DIR/codex-plugins-after.json"
codex mcp get apple-mail --json >"$STATE_DIR/codex-server-after.json"
python3 - "$STATE_DIR" "$PLUGIN_SELECTOR" "$CLAUDE_PLUGIN_SCOPE" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

state = Path(sys.argv[1])
selector = sys.argv[2]
expected_claude_scope = sys.argv[3]
claude_rows = json.loads((state / "claude-plugins-after.json").read_text())
codex_payload = json.loads((state / "codex-plugins-after.json").read_text())
codex_rows = codex_payload.get("installed", codex_payload if isinstance(codex_payload, list) else [])

matching_claude_rows = [row for row in claude_rows if row.get("id") == selector]
claude_ok = len(matching_claude_rows) == 1 and matching_claude_rows[0].get("scope") == expected_claude_scope
codex_ok = any(row.get("pluginId") == selector and row.get("installed") is True for row in codex_rows)
if not claude_ok or not codex_ok:
    print(f"error: central selector verification failed (Claude={claude_ok}, Codex={codex_ok})", file=sys.stderr)
    raise SystemExit(1)

server = json.loads((state / "codex-server-after.json").read_text())
transport = server.get("transport", server)
command = transport.get("command")
args = transport.get("args") or []
cwd = transport.get("cwd")
if not isinstance(command, str) or not command or not isinstance(args, list):
    print("error: Codex MCP launcher verification failed", file=sys.stderr)
    raise SystemExit(1)
runtime = subprocess.run([command, *args, "--doctor"], cwd=cwd, check=False, text=True, capture_output=True)
if runtime.returncode != 0:
    print("error: central runtime bootstrap failed", file=sys.stderr)
    if runtime.stderr:
        print(runtime.stderr.rstrip(), file=sys.stderr)
    raise SystemExit(runtime.returncode or 1)
PY

echo "Verified ${PLUGIN_SELECTOR} from ${MARKETPLACE_URL} in Claude and Codex."
echo "Restart Codex Desktop and Claude Code so MCP schemas reload."
