#!/usr/bin/env bash
# Refresh Apple Mail MCP plugin installs from the local repo checkout.
# Safe to run after git pull. Restart Codex Desktop, Claude Code, and Cursor when done.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c "import json; print(json.load(open('plugin/.claude-plugin/plugin.json'))['version'])")"
echo "apple-mail-mcp refresh (target version ${VERSION})"

git pull --ff-only

codex plugin marketplace upgrade
codex plugin remove apple-mail@apple-mail-mcp 2>/dev/null || true
codex plugin add apple-mail@apple-mail-mcp

claude plugin marketplace update
claude plugin update apple-mail@apple-mail-mcp --scope user

CLAUDE_CACHE="${HOME}/.claude/plugins/cache/apple-mail-mcp/apple-mail"
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

CODEX_CACHE="${HOME}/.codex/plugins/cache/apple-mail-mcp/apple-mail"
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
claude plugin details apple-mail@apple-mail-mcp 2>&1 | head -1
echo "Codex:"
codex plugin list 2>&1 | rg "apple-mail@" || true
echo ""
echo "Done. Restart Codex Desktop / Claude Code / Cursor so MCP schemas reload."
