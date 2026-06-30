#!/usr/bin/env bash
# SessionStart hook for apple-mail-mcp (.claude/settings.json).
# Emits hookSpecificOutput.additionalContext JSON so the project state
# lands in Claude's system prompt at session start.
set -u
cd "$(cd "$(dirname "$0")/../.." && pwd)"

GIT_STATUS="$(git status -s 2>/dev/null | head -20)"
[ -z "$GIT_STATUS" ] && GIT_STATUS="(clean)"

LOG="$(git log --oneline -5 2>/dev/null)"
CHANGELOG_HEAD="$(sed -n '1,25p' CHANGELOG.md 2>/dev/null)"
TODO_HEAD=""
TASKS_LAYOUT=""
if [ -f tasks/todo.md ]; then
    TODO_HEAD="$(head -25 tasks/todo.md 2>/dev/null)"
fi
if [ -f tasks/CLAUDE.md ]; then
    TASKS_LAYOUT="$(awk '/^## Agent requirements \(mandatory\)/,/^## Layout$/' tasks/CLAUDE.md 2>/dev/null | head -35)"
fi

CONTEXT="## apple-mail-mcp project state (auto-injected)

### git status
$GIT_STATUS

### recent commits
$LOG

### CHANGELOG head
$CHANGELOG_HEAD"

if [ -n "$TODO_HEAD" ]; then
    CONTEXT="$CONTEXT

### tasks/todo.md head
$TODO_HEAD"
fi

if [ -n "$TASKS_LAYOUT" ]; then
    CONTEXT="$CONTEXT

### tasks/ agent requirements (mandatory)
$TASKS_LAYOUT"
fi

python3 - "$CONTEXT" <<'PY'
import json, sys
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": sys.argv[1],
    }
}))
PY
