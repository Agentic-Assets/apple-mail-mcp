#!/usr/bin/env bash
# PostToolUse hook for apple-mail-mcp (.claude/settings.json).
#
# Reads the standard hook JSON from stdin:
#   {"tool_name": "Edit"|"Write"|"MultiEdit", "tool_input": {"file_path": "..."}}
#
# Dispatches three checks based on the edited file's path:
#   1. AppleScript syntax (tools/*.py, core.py)        — blocks on parse failure
#   2. Targeted pytest    (tools/*.py)                  — blocks on test failure
#   3. Manifest validate  (tools/*.py + manifests)      — blocks on drift
#
# Exit 2 with stderr surfaces feedback to Claude. Exit 0 stays silent.
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

JSON="$(cat)"
FILE="$(REPO="$REPO" python3 - "$JSON" <<'PY'
import json, os, sys
try:
    d = json.loads(sys.argv[1])
    p = d.get("tool_input", {}).get("file_path", "") or d.get("tool_input", {}).get("path", "")
    if p:
        repo = os.environ.get("REPO", "")
        if repo and p.startswith(repo + "/"):
            p = p[len(repo) + 1:]
        print(p)
except Exception:
    pass
PY
)"

[ -z "$FILE" ] && exit 0

# Skip files outside the source tree fast.
case "$FILE" in
    plugin/apple_mail_mcp/*|tests/*|tools/*|*.json|pyproject.toml) ;;
    *) exit 0 ;;
esac

VENV=".venv/bin"
[ -x "$VENV/python" ] || { exit 0; }  # no venv, no checks
EXIT=0
MSGS=""

append_msg() {
    if [ -z "$MSGS" ]; then
        MSGS="$1"
    else
        MSGS="$MSGS

$1"
    fi
}

# 1. AppleScript syntax check (full builders only)
case "$FILE" in
    plugin/apple_mail_mcp/tools/*.py|plugin/apple_mail_mcp/core.py)
        OUT="$("$VENV/python" .claude/hooks/check_applescript_compiles.py "$FILE" 2>&1)"
        RC=$?
        if [ "$RC" -ne 0 ]; then
            append_msg "$OUT"
            EXIT=2
        fi
        ;;
esac

# 2. Targeted pytest on tool module edits
case "$FILE" in
    plugin/apple_mail_mcp/tools/*.py)
        MOD="$(basename "$FILE" .py)"
        # Match tests/test_<mod>*.py and tests/test_*<mod>*.py
        MATCHES="$(ls tests/test_${MOD}*.py tests/test_*${MOD}*.py 2>/dev/null | sort -u | tr '\n' ' ')"
        if [ -n "$MATCHES" ]; then
            OUT="$("$VENV/pytest" $MATCHES -q --no-header --tb=short 2>&1)"
            RC=$?
            if [ "$RC" -ne 0 ]; then
                # Trim to last 40 lines so we don't drown the hook output
                TRIMMED="$(printf '%s\n' "$OUT" | tail -40)"
                append_msg "Targeted pytest FAILED for $FILE (test files: $MATCHES):
$TRIMMED"
                EXIT=2
            fi
        fi
        ;;
esac

# 3. Manifest drift check
case "$FILE" in
    plugin/apple_mail_mcp/tools/*.py|plugin/.claude-plugin/plugin.json|.claude-plugin/marketplace.json|server.json|pyproject.toml|apple-mail-mcpb/manifest.json)
        OUT="$(bash tools/validate_manifests.sh 2>&1)"
        RC=$?
        if [ "$RC" -ne 0 ]; then
            TRIMMED="$(printf '%s\n' "$OUT" | tail -30)"
            append_msg "Manifest validation FAILED after editing $FILE:
$TRIMMED"
            EXIT=2
        fi
        ;;
esac

if [ -n "$MSGS" ]; then
    printf '%s\n' "$MSGS" >&2
fi
exit $EXIT
