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
  both distributables, runs validator + pytest + module line budget + mcpb smoke.
- **Module line budget:** `dev-check.sh`, CI, and `validate_manifests.py`
  warn on modules over 600 LOC and fail on baseline regression
  (`tests/fixtures/module_line_budget/baseline.json`). See
  `docs/CLAUDE-conventions.md` § Module line budget.
- **Run `code-simplifier:code-simplifier` agent at end of session** —
  REQUIRED before commit on any non-trivial change. Collapses duplication,
  drops dead branches, tightens names; behavior must be preserved.
  Especially after refactors touching many sites, files past ~600 LOC, or
  helpers with >3 near-copies. Skip only for one-line bugfixes, version
  bumps, or docs-only edits.
- **To wrap up a change:** invoke the `finalize-apple-mail-mcp` skill —
  it orchestrates plugin-validator, code-simplifier, doc sync, artifact
  rebuild, and commit/push.
- **`tasks/` layout (mandatory):** read `tasks/todo.md` then `tasks/CLAUDE.md`
  § Agent requirements. New planning artifacts go under `tasks/active/`,
  `tasks/reference/`, or `tasks/archive/` only — never loose `*.md` at
  `tasks/` root. CI enforces via `tools/validate_tasks_layout.py`.

### Quick-pick by change type

| Working on… | Reach for |
|-------------|-----------|
| Perf on large inboxes (O(N²), AppleScript→Python lifts) | python-performance-optimization |
| Timeouts, retry budgets, `AppleScriptTimeout` paths | python-resilience |
| Silent `except` / `on error` skips → `errors[]` arrays | python-error-handling |
| Test gaps (script parse, JSON contract drift) | testing-python · python-testing-patterns |
| Async/asyncio (`asyncio.run()`-in-loop class) | async-python-patterns |
| Code review pass before ship | reviewing-code · code-review · python-anti-patterns |
| Live confirmation a change actually works | verify · run |

### Full Python-development skill index — use these PROACTIVELY, not as last resort

A 5-second skill invocation prevents a 5-minute debug loop. Most edits
to `tools/*.py` should trigger at least one of these. Touch them often;
they are cheap.

| Skill | Use when |
|-------|----------|
| `python-anti-patterns`            | Pre-PR checklist — fast scan for common bad practices |
| `python-code-style`               | Naming, docstrings, lint/format config, ruff rules |
| `python-design-patterns`          | Adding abstractions; KISS / SRP / composition trade-offs |
| `python-error-handling`           | try/except design, validation, partial-failure `errors[]` |
| `python-resilience`               | Retries, exponential backoff, timeouts, fault-tolerant decorators |
| `python-performance-optimization` | Hot paths — `cProfile`, O(N²) loops, AppleScript→Python lifts |
| `python-type-safety`              | Type hints, generics, `Protocol`, mypy/pyright config |
| `python-configuration`            | env vars, `pydantic-settings`, secret externalization |
| `python-resource-management`      | Context managers, streaming responses, deterministic cleanup |
| `python-observability`            | Structured logging, metrics, distributed tracing |
| `python-packaging`                | `pyproject.toml`, PyPI distribution (this repo ships `mcp-apple-mail`) |
| `python-project-structure`        | Module layout, `__all__`, public-API surface design |
| `python-testing-patterns`         | pytest patterns — fixtures, parameterization, mocking |
| `testing-python`                  | Broader test design; pair with `python-testing-patterns` |
| `async-python-patterns`           | asyncio, async/await, `asyncio.run()`-in-loop class of bugs |
| `python-background-jobs`          | Async workers / queues (rare in this repo — flag for future) |
| `uv-package-manager`              | `uv` workflows (this repo uses pip/venv; note for greenfield) |

### Other skills worth invoking frequently in this repo

| Skill / Agent | Use when |
|---------------|----------|
| `reviewing-code`                         | Second-pass review focused on API + pattern clarity |
| `code-review`                            | Run review on current git diff at chosen effort level |
| `security-review`                        | Touching auth, file IO, secrets, destructive operations |
| `verify`                                 | Confirming a change actually works in the running app |
| `run`                                    | Launching the project (`apple-mail` CLI or MCP server) |
| `mcp-builder`                            | New MCP tool design — schema, error contract, naming |
| `plugin-dev:plugin-validator` (agent)    | After tool-count or manifest changes |
| `plugin-dev:skill-reviewer` (agent)      | After editing `plugin/skills/*/SKILL.md` |
| `code-simplifier:code-simplifier` (agent) | **End of session / before commit on any non-trivial change** — REQUIRED, not optional |
| `finalize-apple-mail-mcp`                | Wrapping up any non-trivial change before commit/push |

**Heuristic:** if you are about to edit a Python file in `plugin/` and
have NOT thought about which skill applies, that is the cue to invoke
one. Default picks for this repo: `python-anti-patterns` +
`python-performance-optimization` for any `smart_inbox.py` /
`analytics.py` edit; `python-error-handling` + `python-resilience` for
anything touching `core.run_applescript()` callers.

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
