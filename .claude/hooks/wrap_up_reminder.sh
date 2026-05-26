#!/usr/bin/env bash
# UserPromptSubmit hook for apple-mail-mcp.
# When the user signals wrap-up (ship, finalize, commit-and-push, done, wrap up)
# AND there are unstaged Python changes in plugin/, inject a reminder to run
# the code-simplifier agent before closing the loop. Fires once per matching
# prompt; silent otherwise so it doesn't add noise to every message.
set -u

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

JSON="$(cat)"
PROMPT="$(python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
    print((d.get("prompt") or "").lower())
except Exception:
    pass
' "$JSON" 2>/dev/null)"

[ -z "$PROMPT" ] && exit 0

# Wrap-up signal words. Word-boundary matching avoids false positives like
# "commitment" or "shipment". Generic "commit" alone is excluded for the
# same reason — only "commit and push" / "ready to commit" trigger.
if ! printf '%s' "$PROMPT" | grep -qE '\b(finalize|finalise|ship it|ship this|ready to ship|ready to commit|commit and push|wrap up|wrap-up|all done|we are done|we'\''re done|that is it|that'\''s it|close the loop)\b'; then
    exit 0
fi

CHANGED_PY="$(git status --porcelain 2>/dev/null | awk '{print $NF}' | grep -E '^plugin/apple_mail_mcp/.*\.py$' | head -20)"
[ -z "$CHANGED_PY" ] && exit 0

REMINDER="## Wrap-up reminder: run code-simplifier before commit

You signaled wrap-up and there are unstaged Python changes in \`plugin/\`:

$CHANGED_PY

Per root \`CLAUDE.md\` and \`finalize-apple-mail-mcp\` step 4, the
\`code-simplifier:code-simplifier\` agent is REQUIRED before commit on any
non-trivial change. It collapses duplication, drops dead branches, and
tightens names — behavior must be preserved. Pass it the changed paths
above, then re-run pytest before staging.

Skip only when the diff is a one-line bugfix, a version bump, or
docs-only edit. This is the last automated nudge before commit."

CONTEXT_OUT="$REMINDER" python3 - <<'PY'
import json, os, sys
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": os.environ.get("CONTEXT_OUT", ""),
    }
}))
PY
