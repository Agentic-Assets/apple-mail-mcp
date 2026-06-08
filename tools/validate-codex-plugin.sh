#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not on PATH; skipping Codex plugin install smoke"
  exit 0
fi

if ! codex plugin --help >/dev/null 2>&1; then
  echo "codex CLI does not expose plugin commands; skipping Codex plugin install smoke"
  exit 0
fi

TMP_HOME="$(mktemp -d)"
trap 'rm -rf "$TMP_HOME"' EXIT

export CODEX_HOME="$TMP_HOME"

codex plugin marketplace add .
codex plugin add apple-mail@apple-mail-mcp
codex plugin list --marketplace apple-mail-mcp | grep -F "apple-mail@apple-mail-mcp" >/dev/null

echo "Codex plugin install smoke OK"
