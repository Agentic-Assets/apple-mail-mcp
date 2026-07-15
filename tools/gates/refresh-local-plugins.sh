#!/usr/bin/env bash
# Refresh Apple Mail MCP from its direct-source marketplace identity.
# This helper never mutates the separate shared Agentic Assets marketplace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c "import json; print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])")"
MARKETPLACE_NAME="apple-mail-mcp"
PLUGIN_SELECTOR="apple-mail@${MARKETPLACE_NAME}"
GITHUB_REPO="Agentic-Assets/apple-mail-mcp"
GITHUB_REPO_URL="https://github.com/${GITHUB_REPO}.git"

for command_name in python3 claude codex; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "error: required command is unavailable: ${command_name}" >&2
    exit 1
  fi
done

echo "apple-mail-mcp refresh preflight (target ${PLUGIN_SELECTOR} ${VERSION})"
PYTHONPATH="$ROOT/plugin" python3 tools/validators/validate_manifests.py

STATE_DIR="$(mktemp -d)"
trap 'rm -rf "$STATE_DIR"' EXIT
claude plugin marketplace list --json >"$STATE_DIR/claude-marketplaces.json"
codex plugin marketplace list --json >"$STATE_DIR/codex-marketplaces.json"

python3 - "$STATE_DIR" "$GITHUB_REPO" "$GITHUB_REPO_URL" <<'PY'
import json
import sys
from pathlib import Path

state = Path(sys.argv[1])
repo = sys.argv[2]
repo_url = sys.argv[3]
target = "apple-mail-mcp"

claude_markets = json.loads((state / "claude-marketplaces.json").read_text())
codex_payload = json.loads((state / "codex-marketplaces.json").read_text())
codex_markets = codex_payload.get("marketplaces", [])

problems = []
for item in claude_markets:
    name = item.get("name")
    configured_repo = item.get("repo")
    if name == target and configured_repo != repo:
        problems.append(
            f"Claude marketplace '{target}' already points to {configured_repo!r}, not {repo!r}."
        )
for item in codex_markets:
    name = item.get("name")
    source = (item.get("marketplaceSource") or {}).get("source")
    if name == target and source != repo_url:
        problems.append(
            f"Codex marketplace '{target}' already points to {source!r}, not {repo_url!r}."
        )
if problems:
    print("error: marketplace migration preflight refused:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "No plugin registrations were changed. Resolve the direct-source identity collision, then rerun.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

if python3 - "$STATE_DIR/claude-marketplaces.json" <<'PY'
import json, sys
raise SystemExit(0 if any(x.get("name") == "apple-mail-mcp" for x in json.load(open(sys.argv[1]))) else 1)
PY
then
  claude plugin marketplace update "$MARKETPLACE_NAME"
else
  claude plugin marketplace add "$GITHUB_REPO" --scope user
  claude plugin marketplace update "$MARKETPLACE_NAME"
fi

if claude plugin update "$PLUGIN_SELECTOR" --scope user; then
  :
else
  claude plugin install "$PLUGIN_SELECTOR" --scope user
fi

if python3 - "$STATE_DIR/codex-marketplaces.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
raise SystemExit(0 if any(x.get("name") == "apple-mail-mcp" for x in payload.get("marketplaces", [])) else 1)
PY
then
  codex plugin marketplace upgrade "$MARKETPLACE_NAME"
else
  codex plugin marketplace add "$GITHUB_REPO_URL"
  codex plugin marketplace upgrade "$MARKETPLACE_NAME"
fi
codex plugin add "$PLUGIN_SELECTOR"

claude plugin list --json >"$STATE_DIR/claude-plugins-after.json"
codex plugin list --json >"$STATE_DIR/codex-plugins-after.json"
codex mcp get apple-mail --json >"$STATE_DIR/codex-server-after.json"
python3 - "$STATE_DIR" "$PLUGIN_SELECTOR" "$VERSION" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

state = Path(sys.argv[1])
selector = sys.argv[2]
version = sys.argv[3]
claude_rows = json.loads((state / "claude-plugins-after.json").read_text())
codex_payload = json.loads((state / "codex-plugins-after.json").read_text())
codex_rows = codex_payload.get("installed", codex_payload if isinstance(codex_payload, list) else [])

claude_ok = any(
    row.get("id") == selector and row.get("scope") == "user" and row.get("version") == version
    for row in claude_rows
)
codex_ok = any(
    row.get("pluginId") == selector and row.get("installed") is True and row.get("version") == version
    for row in codex_rows
)
if not claude_ok or not codex_ok:
    print(
        f"error: target verification failed (Claude={claude_ok}, Codex={codex_ok})",
        file=sys.stderr,
    )
    raise SystemExit(1)

server = json.loads((state / "codex-server-after.json").read_text())
transport = server.get("transport", server)
command = transport.get("command")
args = transport.get("args") or []
cwd = transport.get("cwd")
if not isinstance(command, str) or not command or not isinstance(args, list):
    print(
        "error: Codex MCP launcher verification failed",
        file=sys.stderr,
    )
    raise SystemExit(1)
runtime = subprocess.run(
    [command, *args, "--doctor"],
    cwd=cwd,
    check=False,
    text=True,
    capture_output=True,
)
if runtime.returncode != 0:
    print(
        "error: target runtime bootstrap failed",
        file=sys.stderr,
    )
    if runtime.stderr:
        print(runtime.stderr.rstrip(), file=sys.stderr)
    raise SystemExit(runtime.returncode or 1)
PY

echo "Verified ${PLUGIN_SELECTOR} ${VERSION} in Claude and Codex."
echo "Restart Codex Desktop and Claude Code so MCP schemas reload."
