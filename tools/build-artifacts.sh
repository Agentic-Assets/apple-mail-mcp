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
# Zip from INSIDE plugin/ so `.claude-plugin/plugin.json` sits at the zip root.
# Cowork's plugin uploader and `claude plugin validate` both look for the
# manifest at the unzip root — a `plugin/` prefix causes "No manifest found".
# -X strips extra attrs; exclusion list matches README install instructions.
(
  cd plugin && zip -rq -X "../${ZIP_OUT}" . \
    -x 'venv/*' '*/__pycache__/*' '*.pyc' '*.DS_Store'
)

echo "→ Building ${MCPB_OUT} (Claude Desktop bundle)"
bash apple-mail-mcpb/build-mcpb.sh >/dev/null

echo "→ Verifying artifacts"
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh

# Extra MCPB structural smoke: the manifest validator already checks for
# directory entries, but a successful `mcpb unpack` proves Claude Desktop
# will accept the bundle.
if command -v mcpb >/dev/null 2>&1; then
  TMP_MCPB="$(mktemp -d)"
  trap 'rm -rf "${TMP_MCPB}" "${TMP_ZIP:-}"' EXIT
  mcpb unpack "${MCPB_OUT}" "${TMP_MCPB}" >/dev/null
  mcpb validate apple-mail-mcpb/manifest.json >/dev/null
  echo "→ mcpb unpack + validate OK"
else
  echo "→ mcpb CLI not installed; skipping unpack smoke (npm install -g @anthropic-ai/mcpb)"
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
ls -lh "${ZIP_OUT}" "${MCPB_OUT}"
