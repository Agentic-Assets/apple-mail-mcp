#!/usr/bin/env bash
# Rebuild both release artifacts: apple-mail-plugin.zip and the .mcpb bundle.
# Idempotent and safe to re-run. Always invoked by `dev-check.sh release`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
block = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.M)
print(re.search(r'^version\s*=\s*"([^"]+)"', block.group(1), re.M).group(1))
PY
)"

ZIP_OUT="apple-mail-plugin.zip"
MCPB_OUT="apple-mail-mcp-v${VERSION}.mcpb"

echo "→ Building ${ZIP_OUT} (Claude Code plugin)"
rm -f "${ZIP_OUT}"
# -X strips extra attrs; exclusion list matches README install instructions.
zip -rq -X "${ZIP_OUT}" plugin \
  -x 'plugin/venv/*' '*/__pycache__/*' '*.pyc' '*.DS_Store'

echo "→ Building ${MCPB_OUT} (Claude Desktop bundle)"
bash apple-mail-mcpb/build-mcpb.sh >/dev/null

echo "→ Verifying artifacts"
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh

# Extra MCPB structural smoke: the manifest validator already checks for
# directory entries, but a successful `mcpb unpack` proves Claude Desktop
# will accept the bundle.
if command -v mcpb >/dev/null 2>&1; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "${TMP}"' EXIT
  mcpb unpack "${MCPB_OUT}" "${TMP}" >/dev/null
  mcpb validate apple-mail-mcpb/manifest.json >/dev/null
  echo "→ mcpb unpack + validate OK"
else
  echo "→ mcpb CLI not installed; skipping unpack smoke (npm install -g @anthropic-ai/mcpb)"
fi

echo
echo "Artifacts ready:"
ls -lh "${ZIP_OUT}" "${MCPB_OUT}"
