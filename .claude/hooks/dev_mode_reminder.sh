#!/usr/bin/env bash
# SessionStart hook for apple-mail-mcp (.claude/settings.json).
# Pins the developer mindset on every session: this repo ships source for
# THREE distribution channels, and a change here lands in all of them.
#
# Kept separate from session_context.sh so the reminder can be tuned
# without touching the git-state dumper.
set -u

CONTEXT='## You are editing apple-mail-mcp source (dev mode)

This repo ships THREE artifacts from one source tree:
1. Claude Code plugin (`apple-mail-plugin.zip`)
2. PyPI package (`mcp-apple-mail`)
3. Claude Desktop bundle (`apple-mail-mcp-v{VERSION}.mcpb`)

A change here lands in all three. Treat tool source as a public API.

### Standing rules for work in this repo

- **Subagents for implementation, not just research** — see root CLAUDE.md
  § Agent orchestration; plugin-dev experts after tool/manifest/skill edits.
- **AppleScript-in-Python f-strings is parse-checked on edit** by
  `.claude/hooks/check_applescript_compiles.py` (the 3.3.0 awaiting-reply
  regression class). The check runs offline via `osacompile`.
- **Live-verify tool changes** against the TU Exchange inbox (24K msgs)
  before declaring done; mocked tests can pass while the AppleScript
  itself is broken at runtime.
- **Before commits that touch `plugin/`, manifests, `pyproject.toml`,
  or release artifacts:** `bash tools/dev-check.sh release` — rebuilds
  both distributables, runs validator + 416 tests + mcpb smoke.
- **To wrap up a change:** invoke the `finalize-apple-mail-mcp` skill —
  it orchestrates plugin-validator, doc sync, artifact rebuild, and
  commit/push.

### Python skill cheat sheet for common change types

| Working on… | Reach for |
|-------------|-----------|
| Perf on large inboxes (O(N²), AppleScript→Python lifts) | python-performance-optimization |
| Timeouts, retry budgets, `AppleScriptTimeout` paths | python-resilience |
| Silent `except` / `on error` skips → `errors[]` arrays | python-error-handling |
| Test gaps (script parse, JSON contract drift) | testing-python · python-testing-patterns |
| Async/asyncio (`asyncio.run()`-in-loop class) | async-python-patterns |
| Code review pass before ship | reviewing-code · code-review · python-anti-patterns |
| Live confirmation a change actually works | verify · run |

If you are NOT editing this source — i.e. you just want to USE the plugin
to read Mail — this reminder does not apply; look at your installed
plugin instead.'

python3 - "$CONTEXT" <<'PY'
import json, sys
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": sys.argv[1],
    }
}))
PY
