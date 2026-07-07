#!/usr/bin/env bash
# Refresh Apple Mail MCP plugin installs from the GitHub-backed Agentic Assets marketplace.
# Safe to run after git pull. Restart Codex Desktop, Claude Code, and Cursor when done.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c "import json; print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])")"
MARKETPLACE_NAME="Agentic-Assets"
LEGACY_MARKETPLACE_NAME="apple-mail-mcp"
PLUGIN_SELECTOR="apple-mail@${MARKETPLACE_NAME}"
GITHUB_REPO="Agentic-Assets/apple-mail-mcp"
GITHUB_REPO_URL="https://github.com/${GITHUB_REPO}.git"
echo "apple-mail-mcp refresh (target version ${VERSION})"

git pull --ff-only

codex plugin remove "apple-mail@${LEGACY_MARKETPLACE_NAME}" 2>/dev/null || true
codex plugin marketplace remove "$LEGACY_MARKETPLACE_NAME" 2>/dev/null || true
codex plugin marketplace add "$GITHUB_REPO_URL" 2>/dev/null || {
  codex plugin marketplace remove "$MARKETPLACE_NAME" 2>/dev/null || true
  codex plugin marketplace add "$GITHUB_REPO_URL"
}
codex plugin marketplace upgrade
codex plugin remove "$PLUGIN_SELECTOR" 2>/dev/null || true
codex plugin add "$PLUGIN_SELECTOR"

claude plugin marketplace remove "$LEGACY_MARKETPLACE_NAME" 2>/dev/null || true
claude plugin marketplace add "$GITHUB_REPO" --scope user 2>/dev/null || {
  claude plugin marketplace remove "$MARKETPLACE_NAME" 2>/dev/null || true
  claude plugin marketplace add "$GITHUB_REPO" --scope user
}
claude plugin marketplace update "$MARKETPLACE_NAME"
claude plugin update "$PLUGIN_SELECTOR" --scope user 2>/dev/null || \
  claude plugin install "$PLUGIN_SELECTOR" --scope user

CLAUDE_CACHE="${HOME}/.claude/plugins/cache/${MARKETPLACE_NAME}/apple-mail"
if [[ -d "${CLAUDE_CACHE}" ]]; then
  for dir in "${CLAUDE_CACHE}"/*/; do
    [[ -d "${dir}" ]] || continue
    base="$(basename "${dir}")"
    if [[ "${base}" != "${VERSION}" ]]; then
      echo "remove stale Claude cache ${base}"
      rm -rf "${dir}"
    fi
  done
fi

CODEX_CACHE="${HOME}/.codex/plugins/cache/${MARKETPLACE_NAME}/apple-mail"
if [[ -d "${CODEX_CACHE}" ]]; then
  for dir in "${CODEX_CACHE}"/*/; do
    [[ -d "${dir}" ]] || continue
    base="$(basename "${dir}")"
    if [[ "${base}" != "${VERSION}" ]]; then
      echo "remove stale Codex cache ${base}"
      rm -rf "${dir}"
    fi
  done
fi

echo ""
echo "Claude:"
claude plugin details "$PLUGIN_SELECTOR" 2>&1 | head -1
echo "Codex:"
codex plugin list 2>&1 | rg "apple-mail@" || true
echo ""
echo "Done. Restart Codex Desktop / Claude Code / Cursor so MCP schemas reload."
