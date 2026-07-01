#!/usr/bin/env bash
# Rebuild both release artifacts: apple-mail-plugin.zip and the .mcpb bundle.
# Idempotent and safe to re-run. Always invoked by `dev-check.sh release`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
block = re.search(r"^\[project\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.M)
print(re.search(r'^version\s*=\s*"([^"]+)"', block.group(1), re.M).group(1))
PY
)"

ZIP_OUT="apple-mail-plugin.zip"
PLUGIN_OUT="apple-mail.plugin"
MCPB_OUT="apple-mail-mcp-v${VERSION}.mcpb"

echo "→ Pruning stale apple-mail-mcp-v*.mcpb (keeping ${MCPB_OUT})"
for stale in apple-mail-mcp-v*.mcpb; do
  [[ -e "${stale}" ]] || continue
  if [[ "${stale}" != "${MCPB_OUT}" ]]; then
    echo "  removing ${stale}"
    rm -f "${stale}"
  fi
done

echo "→ Building ${ZIP_OUT} (Claude Code plugin)"
rm -f "${ZIP_OUT}" "${PLUGIN_OUT}"
# Zip from INSIDE plugin/ so `.claude-plugin/plugin.json` sits at the zip root.
# Cowork's plugin uploader and `claude plugin validate` both look for the
# manifest at the unzip root — a `plugin/` prefix causes "No manifest found".
#
# Flags:
#   -X  strip extra Unix attrs (consistent bytes)
#   -D  no directory entries — Cowork's web validator (and the MCPB
#       extractor we already fixed) choke on zero-byte `dir/` entries.
#       Same defect that broke the .mcpb upload; fix it here too.
(
  cd plugin && zip -rq -X -D "../${ZIP_OUT}" . \
    -x 'venv/*' '*/__pycache__/*' '*.pyc' '*.DS_Store' 'CLAUDE.md' '*/CLAUDE.md' \
       '.env' '.env.*' '*.log' '*.tmp' '*.bak' '*.swp'
)

# Mirror the same bytes as apple-mail.plugin for Claude Desktop "Add Custom
# Plugin" UI (and Cowork chat-attach install). The Desktop installer accepts
# the .plugin extension as a synonym for the Claude Code plugin zip; keeping
# byte-identical contents lets validate_manifests treat them as one artifact.
cp "${ZIP_OUT}" "${PLUGIN_OUT}"

echo "→ Building ${MCPB_OUT} (Claude Desktop bundle)"
bash apple-mail-mcpb/build-mcpb.sh >/dev/null

echo "→ Verifying artifacts"
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/gates/validate_manifests.sh

# Extra MCPB structural smoke: the manifest validator already checks for
# directory entries, but a successful `mcpb unpack` proves Claude Desktop
# will accept the bundle.
MCPB_CMD=()
if command -v mcpb >/dev/null 2>&1; then
  MCPB_CMD=(mcpb)
elif command -v npx >/dev/null 2>&1; then
  MCPB_CMD=(npx -y @anthropic-ai/mcpb)
fi

if [[ ${#MCPB_CMD[@]} -gt 0 ]]; then
  TMP_MCPB="$(mktemp -d)"
  trap 'rm -rf "${TMP_MCPB}" "${TMP_ZIP:-}"' EXIT
  "${MCPB_CMD[@]}" unpack "${MCPB_OUT}" "${TMP_MCPB}" >/dev/null
  "${MCPB_CMD[@]}" validate apple-mail-mcpb/manifest.json >/dev/null
  echo "→ mcpb unpack + validate OK"
else
  echo "→ mcpb CLI not installed and npx not found; skipping unpack smoke"
fi

# Plugin-zip structural smoke: unzip and validate as Cowork's plugin
# uploader does — `.claude-plugin/plugin.json` must live at zip root, AND
# `--strict` must pass (Cowork promotes warnings to errors; default mode
# does not, which is why our older zip passed locally but failed Cowork).
if command -v claude >/dev/null 2>&1; then
  TMP_ZIP="$(mktemp -d)"
  (cd "${TMP_ZIP}" && unzip -q "${ROOT}/${ZIP_OUT}")
  claude plugin validate "${TMP_ZIP}" --strict >/dev/null
  echo "→ claude plugin validate --strict OK (manifest at zip root, no warnings)"
else
  echo "→ claude CLI not on PATH; skipping plugin-zip unpack smoke"
fi

echo
echo "Artifacts ready:"
ls -lh "${ZIP_OUT}" "${PLUGIN_OUT}" "${MCPB_OUT}"
