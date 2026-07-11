#!/usr/bin/env bash
# Regenerate the pinned macOS arm64 CPython 3.13 offline runtime payload.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3.13}"
PIP_COMPILE="${PIP_COMPILE:-pip-compile}"
LOCK="plugin/requirements.lock"
INPUT="plugin/requirements.in"
WHEELHOUSE="plugin/wheelhouse"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "error: python3.13 is required" >&2; exit 1; }
command -v "$PIP_COMPILE" >/dev/null 2>&1 || { echo "error: pip-compile is required (install pip-tools in a build environment)" >&2; exit 1; }

"$PIP_COMPILE" --generate-hashes --strip-extras --output-file "$LOCK" "$INPUT"
rm -rf "$WHEELHOUSE"
mkdir -p "$WHEELHOUSE"
"$PYTHON_BIN" -m pip download --require-hashes --only-binary=:all: --dest "$WHEELHOUSE" -r "$LOCK"
find "$WHEELHOUSE" -type f -name '*.whl' -print | sort
