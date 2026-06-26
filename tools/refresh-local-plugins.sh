#!/usr/bin/env bash
# Install or update Apple Mail MCP from this checkout for user-scope Claude Code
# and the current user's Codex config. Safe to re-run after git pull.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PLUGIN="apple-mail@apple-mail-mcp"
MARKETPLACE="apple-mail-mcp"
CLI_VENV="${APPLE_MAIL_MCP_CLI_VENV:-${HOME}/.local/share/apple-mail-mcp/venv}"
CLI_BIN="${APPLE_MAIL_MCP_USER_BIN:-${HOME}/.local/bin}"

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "error: ${name} CLI is required but was not found on PATH" >&2
    exit 1
  fi
}

user_base_bin() {
  python3 - <<'PY'
import site
print(site.USER_BASE + "/bin")
PY
}

find_user_command() {
  local name="$1"
  local user_bin
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi
  if [[ -x "${CLI_BIN}/${name}" ]]; then
    echo "${CLI_BIN}/${name}"
    return 0
  fi
  user_bin="$(user_base_bin)"
  if [[ -x "${user_bin}/${name}" ]]; then
    echo "${user_bin}/${name}"
    return 0
  fi
  return 1
}

version() {
  python3 -c "import json; print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])"
}

install_cli() {
  if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$ROOT"
    return
  fi

  python3 -m venv "$CLI_VENV"
  "$CLI_VENV/bin/python" -m pip install --upgrade pip
  "$CLI_VENV/bin/python" -m pip install --upgrade "$ROOT"
  mkdir -p "$CLI_BIN"
  ln -sf "$CLI_VENV/bin/mcp-apple-mail" "$CLI_BIN/mcp-apple-mail"
  if [[ -L "${CLI_BIN}/apple-mail" && "$(readlink "${CLI_BIN}/apple-mail")" == "${CLI_VENV}/bin/apple-mail" ]]; then
    rm -f "${CLI_BIN}/apple-mail"
  fi
}

require_command git
require_command python3
require_command claude
require_command codex

echo "apple-mail-mcp user install/update from ${ROOT}"
if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  git pull --ff-only
else
  echo "no upstream branch configured; skipping git pull"
fi
VERSION="$(version)"
echo "target version: ${VERSION}"

echo ""
echo "Apple Mail CLI"
install_cli
MCP_APPLE_MAIL_CLI="$(find_user_command mcp-apple-mail)" || {
  echo "error: mcp-apple-mail command was installed but is not on PATH or ${CLI_BIN}" >&2
  exit 1
}
"$CLI_VENV/bin/apple-mail" --version
"$MCP_APPLE_MAIL_CLI" --help >/dev/null

echo ""
echo "Codex user plugin"
codex plugin remove "$PLUGIN" 2>/dev/null || true
codex plugin marketplace remove "$MARKETPLACE" 2>/dev/null || true
codex plugin marketplace add ./
codex plugin add "$PLUGIN"
codex plugin list --marketplace "$MARKETPLACE" | grep -F "$PLUGIN" >/dev/null
codex mcp get apple-mail --json >/dev/null

echo ""
echo "Claude Code user plugin"
claude plugin uninstall "$PLUGIN" --scope user --keep-data --yes 2>/dev/null || true
claude plugin marketplace remove "$MARKETPLACE" 2>/dev/null || true
claude plugin marketplace add ./ --scope user
claude plugin install "$PLUGIN" --scope user
claude plugin details "$PLUGIN" | grep -F "apple-mail ${VERSION}" >/dev/null

echo ""
echo "Installed Apple Mail MCP ${VERSION} MCP server CLI, Codex plugin, and Claude Code user plugin."
echo "Restart Codex Desktop, Claude Code, and Cursor so MCP schemas reload."
