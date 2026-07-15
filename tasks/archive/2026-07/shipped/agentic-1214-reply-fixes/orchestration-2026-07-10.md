# AGENTIC-1214 reply fixes: orchestration record (2026-07-10)

**Branch:** `fix/agentic-1214-reply-body-truncation` (off `main` @ `122314e`, v3.10.1)
**Linear:** [AGENTIC-1214](https://linear.app/agenticassets/issue/AGENTIC-1214/apple-mail-mcp-reply-to-email-hard-truncates-long-reply-body-320-330) (High). Related: AGENTIC-781 (parent tracker), AGENTIC-1192 (same truncation on TU), AGENTIC-973 (reply_to_all CC drop, unreproduced), AGENTIC-1191 (scan perf, likely out of scope), AGENTIC-1003 (environment, likely out of scope).
**Workflow run:** `wf_bf030bd1-31b` (script `agentic-1214-reply-fixes`).

## Root-cause chain (confirmed by orchestrator before dispatch)

1. `plugin/apple_mail_mcp/tools/compose/reply_scripts.py:470`: the native reply path inserts the entire `reply_body` with ONE System Events `keystroke replyBodyText` call. Long strings drop their tail around 320-480 chars (Bug 1); short strings sometimes come out all-caps from shift-state mangling (Bug 3).
2. `plugin/apple_mail_mcp/tools/compose/saved_draft_checks.py:59`: post-save verification matches only `_first_non_empty_line(reply_body)`, so a truncated body whose first line survived passes.
3. AppleScript string comparison ignores case by default, so ALL CAPS drafts also passed the needle check.
4. `manage_drafts(action="create")` silently ignores `in_reply_to` (documented only for `action="find"`), producing unthreaded pseudo-replies (Bug 2).

Constraint: clipboard paste for the reply body was used before and reverted (pasteboard clobber, wrong-thread leak); the native path must never reassign `content of replyMessage` (flattens rich quote + logo signature).

## Phases and model tiering

| Phase | Agents | Model |
|-------|--------|-------|
| Recon | code map, drafts path, keystroke domain research, Linear triage (4 parallel) | Sonnet |
| Design | implementation plan | Opus |
| Design | adversarial design review + live-check mandates | Fable |
| Implement | typed-path fix + full-body verification; manage_drafts contract (2 parallel); then docs/skills/CHANGELOG | Sonnet |
| Verify | code-simplifier, full gates, adversarial diff review (Fable), conditional fixer (Opus), live Mail check (solo, draft-only) | mixed |

Reports land in `tasks/active/agentic-1214-reply-fixes/reports/`. Ship steps (version bump, CHANGELOG heading, `dev-check.sh release`, push, Linear comments, return to main) stay with the orchestrator. No PR (not authorized for this repo); merge needs Cayman's explicit phrase.
